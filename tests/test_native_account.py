"""Opt-in isolated Excel COM integration test; never opens the live workbook.

Run with ACCOUNT_NATIVE_TESTS=1 to exercise the actual native save/replace path.
"""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import account


@unittest.skipUnless(os.environ.get('ACCOUNT_NATIVE_TESTS') == '1', 'opt-in native Excel test')
class NativeExcelTests(unittest.TestCase):
    def test_reviewed_import_recalculates_table_and_keeps_original_fields(self):
        with tempfile.TemporaryDirectory(prefix='.account-fixture-', dir=Path(__file__).parent) as temp:
            directory = Path(temp)
            live = directory / '个人财务系统.xlsx'
            original = account.normalize_record({'平台': '支付宝', '交易时间': '2026-10-01 12:00:00',
                '交易对方':'合成商户', '商品/说明':'测试', '原始金额':'10.00', '交易订单号':'00000000000000000001',
                '最终财务事件':'PERSONAL', '统计口径':'PERSONAL', '计入消费金额':'10.00', '资金池':'运行账户'})
            fixture = directory/'fixture.json'
            fixture.write_text(json.dumps({'headers':account.HEADERS,'record':original,'sheets':account.SHEETS,'path':str(live)},ensure_ascii=False),encoding='utf-8-sig')
            setup = r'''
$ErrorActionPreference='Stop'
$data=Get-Content -LiteralPath 'FIXTURE_PATH' -Raw -Encoding UTF8 | ConvertFrom-Json
$app=New-Object -ComObject Excel.Application
$app.Visible=$false; $app.DisplayAlerts=$false; $app.AutomationSecurity=3
$book=$null
try {
 $book=$app.Workbooks.Add()
 while($book.Worksheets.Count -lt 4){[void]$book.Worksheets.Add([Type]::Missing,$book.Worksheets.Item($book.Worksheets.Count))}
 for($i=1;$i -le 4;$i++){$book.Worksheets.Item($i).Name=$data.sheets[$i-1]}
 $sheet=$book.Worksheets.Item('01_账单流水')
 for($i=1;$i -le 38;$i++){$sheet.Cells.Item(1,$i).Value2=[string]$data.headers[$i-1]}
 foreach($property in $data.record.PSObject.Properties){
  $idx=[Array]::IndexOf($data.headers,$property.Name)+1
  $cell=$sheet.Cells.Item(2,$idx)
  if($property.Name -eq '交易时间'){$cell.Value2=[double]([DateTime]::Parse($property.Value).ToOADate());$cell.NumberFormat='yyyy-mm-dd hh:mm:ss'}
  elseif($property.Name -in @('原始金额','计入消费金额')){$cell.Value2=[double]$property.Value}
  else{$cell.NumberFormat='@';$cell.Value2=[string]$property.Value}
 }
 $table=$sheet.ListObjects.Add(1,$sheet.Range('A1:AL2'),[Type]::Missing,1)
 $table.Name='LedgerTable'
 $sheet.Range('AK2').Formula='=TEXT([@交易时间],"yyyy-mm")'
 $sheet.Range('AL2').Formula='=IF([@计入消费金额]>0,COUNTIFS(LedgerTable[月份],[@月份],LedgerTable[计入消费金额],">"&[@计入消费金额])+1,"")'
 $stats=$book.Worksheets.Item('03_统计数据'); $pools=$book.Worksheets.Item('02_资金池'); $dash=$book.Worksheets.Item('00_Dashboard')
 $stats.Range('B2').Formula='=TEXT(MAX(LedgerTable[交易时间]),"yyyy-mm")'
 $stats.Range('Q2').Formula='=SUM(LedgerTable[计入消费金额])'
 $stats.Range('Q3').Formula='=SUM(LedgerTable[计入消费金额])'
 $stats.Range('Q4').Value2=[double]0
 $stats.Range('J2').Formula='=ROWS(LedgerTable[记录ID])'
 $stats.Range('U2:U5').Value2=[double]0
 $pools.Range('B3').Formula="='03_统计数据'!B2"
 $pools.Range('B4').Value2=[double]([DateTime]::new(2026,11,1).ToOADate())
 $pools.Range('B4').NumberFormat='yyyy-mm'
 $poolNames=@('运行账户','随心消费','旅行/体验','成长/项目/耐用品')
 for($j=7;$j -le 10;$j++){
  $pools.Cells.Item($j,1).Value2=$poolNames[$j-7]
  $pools.Cells.Item($j,6).Formula=('=SUMIF(LedgerTable[资金池],A'+$j+',LedgerTable[计入消费金额])')
  $pools.Cells.Item($j,7).Value2=[double]0
 }
 $dash.Range('A7').Formula="='03_统计数据'!Q2"
 $dash.Range('E7').Formula="='03_统计数据'!Q4"
 $dash.Range('I7').Formula="=SUM('02_资金池'!F7:F10)"
 $dash.Range('M7').Formula='=IF(DATEVALUE(''02_资金池''!B3&"-01")<''02_资金池''!B4,"未启用",SUM(''02_资金池''!G7:G10))'
 $app.CalculateFullRebuild()
 $book.SaveAs($data.path,51)
}finally{if($book){$book.Close($false)};$app.Quit();[GC]::Collect();[GC]::WaitForPendingFinalizers()}
'''.replace('FIXTURE_PATH',str(fixture).replace("'","''"))
            encoded = base64.b64encode(setup.encode('utf-16-le')).decode()
            subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],check=True,capture_output=True,timeout=120)
            row = dict(original)
            row.update({'交易时间':'2026-10-02 12:00:00','原始金额':'0.00','交易订单号':'00000000000000000002','计入消费金额':'0.00'})
            row.pop('记录ID')
            plan = account.plan_import([original], {'records':[row], 'updates':[{'记录ID':original['记录ID'], 'changes':{'退款调整':'2.00','计入消费金额':'8.00'},'reason':'合成退款','confirmation_source':'测试'}]})
            report = account.safe_write(live,'Import',plan)
            self.assertTrue(report['ok'],report['errors'])
            self.assertTrue(report['native_recalculation_verified'])
            rows,_ = account.ledger(live)
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[0]['交易订单号'], original['交易订单号'])
            self.assertEqual(account.money(rows[0]['原始金额']),account.money('10'))
            self.assertEqual(account.money(rows[0]['计入消费金额']),account.money('8'))
            self.assertEqual(rows[1]['月份'],'2026-10')
            self.assertIsNone(rows[1]['月内支出排名'])
            second = account.plan_import(rows, {'records':[row]})
            self.assertEqual(second['duplicates'],1)
            self.assertTrue(account.verify(live,require_native=True)['ok'])
            cached = account.openpyxl.load_workbook(live,read_only=True,data_only=True)
            try: self.assertEqual(cached['00_Dashboard']['M7'].value,'未启用')
            finally: cached.close()
            self.assertFalse(list(directory.glob('.account-*')))


if __name__ == '__main__': unittest.main()
