@echo off
py -3 "%~dp0ag.py" %* 2>nul || python "%~dp0ag.py" %*
