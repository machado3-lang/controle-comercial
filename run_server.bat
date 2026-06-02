@echo off
cd /d "C:\Controle de Serviços"
python -m uvicorn main:app --host 0.0.0.0 --port 3000 --reload