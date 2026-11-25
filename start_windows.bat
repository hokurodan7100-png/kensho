@echo off
rem Start Kensho Manager (admin app + worker).
rem Register this file in Task Scheduler with the "At log on" trigger.
cd /d %~dp0
start "kensho-app" /min .venv\Scripts\python.exe app.py
start "kensho-worker" /min .venv\Scripts\python.exe worker.py
