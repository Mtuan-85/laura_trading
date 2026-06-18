@echo off
set BRAVE="C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
set PROFILE=D:\CDP_Browser\brave-grok-profile
%BRAVE% --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run https://grok.com/imagine
