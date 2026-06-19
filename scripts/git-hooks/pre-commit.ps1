$ErrorActionPreference = "Stop"

& .\.runtime\python\python.exe scripts\enforce_batch_gate.py --mode pre-commit
exit $LASTEXITCODE
