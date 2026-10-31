"""View-only XLSX export. Inline strings cannot become spreadsheet formulas."""
import os
from pathlib import Path
import tempfile
from xml.etree.ElementTree import Element, SubElement, tostring
from zipfile import ZipFile, ZIP_DEFLATED

import account
import ledger_calculation
import ledger_store

NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def _sheet(rows, money_columns=()):
    sheet = Element('worksheet', xmlns=NS)
    SubElement(sheet,'dimension',ref=f'A1:{account.openpyxl.utils.get_column_letter(len(rows[0]))}{len(rows)}')
    views = SubElement(sheet,'sheetViews')
    view = SubElement(views,'sheetView',workbookViewId='0')
    SubElement(view,'pane',ySplit='1',topLeftCell='A2',activePane='bottomLeft',state='frozen')
    columns = SubElement(sheet,'cols')
    for col in range(1,len(rows[0])+1):
        SubElement(columns,'col',min=str(col),max=str(col),width='22',customWidth='1')
    data = SubElement(sheet,'sheetData')
    for row_index, values in enumerate(rows,1):
        row = SubElement(data,'row',r=str(row_index))
        for col_index, value in enumerate(values,1):
            cell = SubElement(row,'c',r=f'{account.openpyxl.utils.get_column_letter(col_index)}{row_index}')
            cell.set('s','1' if row_index==1 else '2' if col_index in money_columns and value is not None else '0')
            if value is None: continue
            if col_index in money_columns and row_index>1 and str(value).strip():
                SubElement(cell,'v').text = str(account.money(value))
            else:
                cell.set('t','inlineStr')
                text = SubElement(SubElement(cell,'is'),'t',{'{http://www.w3.org/XML/1998/namespace}space':'preserve'})
                text.text = str(value)
    SubElement(sheet,'autoFilter',ref=f'A1:{account.openpyxl.utils.get_column_letter(len(rows[0]))}{len(rows)}')
    return tostring(sheet,encoding='utf-8',xml_declaration=True)


def export_excel(database, output):
    database, output = Path(database).resolve(), Path(output).resolve()
    if output.suffix.lower() != '.xlsx' or output.exists() or output == database:
        raise ValueError('Excel导出必须使用新的.xlsx路径，不能覆盖已有文件或账本来源')
    state = ledger_store.read_state(database)
    rows = [account.HEADERS[:36]] + [[r.get(key) for key in account.HEADERS[:36]] for r in state['rows']]
    budgets = [['生效月份',*account.POOLS]] + [[r['生效月份'],*[r[p] for p in account.POOLS]] for r in state['rates']]
    month_rows = [['月份','个人净消费','结构性支出','待归类','安全积累',
                   *[p+'消费' for p in account.POOLS],*[p+'期末' for p in account.POOLS]]]
    import dashboard_data
    calendar = dashboard_data.current_month()
    start = min([account.timestamp(r['交易时间'])[:7] for r in state['rows']] + [calendar])
    last = max([account.timestamp(r['交易时间'])[:7] for r in state['rows']] + [calendar])
    month = start
    while month<=last:
        view = ledger_calculation.calculate_month(state,month,calendar)
        amounts = [view['summary'][k] for k in ['personalCents','structuralCents','unknownCents','safetyCents']]
        amounts += [p['spentCents'] for p in view['pools']] + [p['endingCents'] for p in view['pools']]
        month_rows.append([month,*[str(account.Decimal(v)/100) for v in amounts]])
        year, number = map(int,month.split('-')); month=f'{year+number//12:04d}-{number%12+1:02d}'
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='account-export-',dir=output.parent) as directory:
        draft = Path(directory)/'view.xlsx'
        sheets = [('账单流水',rows,{8,14,15,18}),('预算参数',budgets,{2,3,4,5}),
                  ('月度统计',month_rows,set(range(2,len(month_rows[0])+1)))]
        with ZipFile(draft,'w',ZIP_DEFLATED) as package:
            content = Element('Types',xmlns='http://schemas.openxmlformats.org/package/2006/content-types')
            SubElement(content,'Default',Extension='rels',ContentType='application/vnd.openxmlformats-package.relationships+xml')
            SubElement(content,'Default',Extension='xml',ContentType='application/xml')
            SubElement(content,'Override',PartName='/xl/workbook.xml',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml')
            SubElement(content,'Override',PartName='/xl/styles.xml',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml')
            book = Element('workbook',xmlns=NS); entries=SubElement(book,'sheets')
            relationships=Element('Relationships',xmlns='http://schemas.openxmlformats.org/package/2006/relationships')
            for index,(name,values,money) in enumerate(sheets,1):
                SubElement(entries,'sheet',name=name,sheetId=str(index),attrib={f'{{{REL}}}id':f'rId{index}'})
                SubElement(relationships,'Relationship',Id=f'rId{index}',Type=REL+'/worksheet',Target=f'worksheets/sheet{index}.xml')
                SubElement(content,'Override',PartName=f'/xl/worksheets/sheet{index}.xml',ContentType='application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml')
                package.writestr(f'xl/worksheets/sheet{index}.xml',_sheet(values,money))
            SubElement(relationships,'Relationship',Id='styles',Type=REL+'/styles',Target='styles.xml')
            package.writestr('[Content_Types].xml',tostring(content))
            package.writestr('xl/workbook.xml',tostring(book))
            package.writestr('xl/_rels/workbook.xml.rels',tostring(relationships))
            package.writestr('_rels/.rels',f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>')
            package.writestr('xl/styles.xml',f'<styleSheet xmlns="{NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf/><xf fontId="1" applyFont="1"/><xf numFmtId="4" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
        if ledger_store.source_hash(database)!=state['sourceHash']:
            raise RuntimeError('导出期间账本发生变化，未交付旧版本')
        # Atomic, same-volume creation also refuses a concurrently-created file.
        os.link(draft,output)
    return dict(ok=True,output=str(output),rows=len(state['rows']),sourceHash=state['sourceHash'],viewOnly=True)
