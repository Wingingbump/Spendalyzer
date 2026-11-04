@echo off
REM Double-clickable wrapper around dev.ps1.
REM
REM -ExecutionPolicy Bypass applies to this one invocation only; it does not
REM change the machine's policy. Without it an unsigned .ps1 refuses to run
REM under the default Restricted/RemoteSigned setting, which is the usual
REM reason "just run the script" fails on a fresh Windows box.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0dev.ps1" %*
