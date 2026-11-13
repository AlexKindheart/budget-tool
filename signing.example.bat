@echo off
rem Code signing for build-installer.bat. Copy this file to signing.bat (signing.bat is never committed) and set
rem SIGN_CMD to the command that signs one file; build-installer.bat adds the file name at the end.
rem
rem Signed installers don't show "Windows protected your PC". You need a code-signing certificate, e.g.:
rem   - Azure Trusted Signing (about $10 a month; Microsoft's own, works with signtool),
rem   - SignPath.io (free for open-source projects), or
rem   - a certificate from a certificate authority (Sectigo, DigiCert, SSL.com...).
rem signtool.exe comes with the Windows SDK:  winget install Microsoft.WindowsSDK.10.0.22621
rem
rem A certificate in a .pfx file (keep the file and its password out of this folder):
rem set SIGN_CMD="C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe" sign /f "C:\Keys\budgettool.pfx" /p YOUR_PASSWORD /fd sha256 /tr http://timestamp.digicert.com /td sha256
rem
rem A certificate installed in Windows (or on a hardware token), picked by its name:
rem set SIGN_CMD="C:\Program Files (x86)\Windows Kits\10\bin\10.0.22621.0\x64\signtool.exe" sign /n "Your Name" /fd sha256 /tr http://timestamp.digicert.com /td sha256
