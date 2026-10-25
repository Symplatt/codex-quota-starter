"""Reproducible allowlisted source/Windows launcher archive, excluding runtime data."""
import hashlib
from pathlib import Path
import tempfile
import zipfile
from version import VERSION

root=Path(__file__).resolve().parent
files=['windows_settings.py','Autostart-Control.ps1','app.py','core.py','rpc.py','supervisor.py','diagnose.py','ui.html','test_quota.py',
       'Start.ps1','Start.cmd','Stop.ps1','Install.ps1','Install.cmd','Install-Autostart.ps1',
       'Install-Autostart.cmd','Uninstall-Autostart.ps1','Uninstall.ps1','README.md',
       'MECHANISM.md','VALIDATION.md','CHANGELOG.md','requirements.txt','LICENSE',
       'build_release.py','.gitignore','.gitattributes','.github/workflows/test.yml']
files += ['AGENTS.md','help.html','favicon.svg','version.py','test_schedule.py','test_ui.cjs','test_audit.py','test_statistics.py','test_update.py','test_weekly_reset.py']
files += ['test_connection_baseline.py','test_fixtures/connection-baseline.json']
files += ['test_connection.py','test_fixtures/connection-changes.json']
files += ['tasks.py','tasks.js','test_tasks.py','test_tasks_ui.cjs']
files += ['schedule_queue.py','test_schedule_queue.py']
out=root/'dist';out.mkdir(exist_ok=True)
target=out/f'quota-starter-{VERSION}-windows.zip'
# Validate all inputs before replacing any existing release artifact.
sources={name:(root/name).read_text(encoding='utf-8').encode('utf-8') for name in sorted(files)}
with tempfile.NamedTemporaryFile(dir=out,prefix='build-',suffix='.zip.tmp',delete=False) as pending:
    staged=Path(pending.name)
staged_checksum=staged.with_suffix('.sha256.tmp')
try:
    with zipfile.ZipFile(staged,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,data in sources.items():
            info=zipfile.ZipInfo('quota-starter/'+name,date_time=(2026,10,9,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o644<<16
            archive.writestr(info,data)
    digest=hashlib.sha256(staged.read_bytes()).hexdigest()
    staged_checksum.write_text(digest+'  '+target.name+'\n',encoding='ascii')
    staged.replace(target)
    staged_checksum.replace(out/'SHA256SUMS.txt')
finally:
    staged.unlink(missing_ok=True)
    staged_checksum.unlink(missing_ok=True)
print(target)
print(digest)
