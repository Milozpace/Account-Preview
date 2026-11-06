"""V2 presentation from one consistent SQLite business state."""
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile

import account
import dashboard_data
import ledger_calculation
import ledger_source
import ledger_store


def sync_database_snapshot(database):
    database = Path(database).resolve()
    if 'archive' in {p.lower() for p in database.parts}:
        raise ValueError('拒绝从历史存档生成当前展示')
    runtime = ledger_source.runtime(database)
    with dashboard_data.sync_lock(runtime):
        state = ledger_store.read_state(database)
        month = dashboard_data.current_month()
        months = dashboard_data.source_tracking_months(database, state, month)
        views = [dashboard_data.build_month(state['rows'], selected,
                    ledger_calculation.calculate_month(state, selected, month)) for selected in months]
        last = max((account.timestamp(row['交易时间']) for row in state['rows']),default=None)
        snapshot = dict(schemaVersion=dashboard_data.SCHEMA_VERSION,sourceHash=state['sourceHash'],calendarMonth=month,
                        generatedAt=datetime.now(account.TZ).isoformat(),ledgerCount=len(state['rows']),
                        sourceLastRecordedAt=last.replace(' ','T')+'+08:00' if last else None,months=views)
        # The App validator also audits all month internals before either export.
        import app_export
        app_export.validate_app_snapshot({**snapshot,'schemaVersion':app_export.SCHEMA_VERSION,
            'snapshotKind':'account-ledger-view','coverage':{'kind':app_export.COVERAGE_KIND,'months':months}})
        payload = json.dumps(snapshot,ensure_ascii=False,separators=(',',':'),allow_nan=False)
        with tempfile.TemporaryDirectory(prefix='sqlite-snapshot-',dir=runtime) as folder:
            draft = Path(folder) / 'dashboard.json'
            draft.write_text(payload,encoding='utf-8')
            ledger_source.assert_active(database)
            if ledger_store.source_hash(database) != state['sourceHash'] or dashboard_data.current_month() != month:
                raise RuntimeError('导出期间账本或月份已变化，已保留有效快照')
            if dashboard_data.source_tracking_months(database, state, month) != months:
                raise RuntimeError('导出期间展示配置已变化，已保留有效快照')
            os.replace(draft,runtime / 'dashboard.json')
        return dict(ok=True,generatedAt=snapshot['generatedAt'],months=months,ledgerCount=len(state['rows']),
                    sourceHash=state['sourceHash'],calculationEngine='python',originalUnchanged=True)
