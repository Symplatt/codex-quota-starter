"""Reproducible allowlisted source/Windows launcher archive, excluding runtime data."""
import hashlib
from pathlib import Path
import zipfile
from version import VERSION

root=Path(__file__).resolve().parent
files=['windows_settings.py','Autostart-Control.ps1','app.py','core.py','rpc.py','supervisor.py','diagnose.py','ui.html','test_quota.py',
       'Start.ps1','Start.cmd','Stop.ps1','Install.ps1','Install.cmd','Install-Autostart.ps1',
       'Install-Autostart.cmd','Uninstall-Autostart.ps1','Uninstall.ps1','README.md',
       'MECHANISM.md','VALIDATION.md','CHANGELOG.md','requirements.txt','LICENSE',
       'build_release.py','.gitignore','.gitattributes','.github/workflows/test.yml']
files += ['help.html','version.py','test_schedule.py']
out=root/'dist';out.mkdir(exist_ok=True)
target=out/f'quota-starter-{VERSION}-windows.zip'
with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
    for name in sorted(files):
        data=(root/name).read_text(encoding='utf-8').encode('utf-8')
        info=zipfile.ZipInfo('quota-starter/'+name,date_time=(2026,10,9,0,0,0))
        info.compress_type=zipfile.ZIP_DEFLATED
        info.external_attr=0o644<<16
        archive.writestr(info,data)
digest=hashlib.sha256(target.read_bytes()).hexdigest()
(out/'SHA256SUMS.txt').write_text(digest+'  '+target.name+'\n',encoding='ascii')
print(target)
print(digest)
