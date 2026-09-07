# StepInBel Streamlit UI

StepInBel is an Energent project. Made by Joannes Laveyne of Plan-D.io, for
Energent cvba. The project is supported by the FPS Economy.

This directory contains the Streamlit product interface. `ui/app.py` is the
supported entry point. After `setup.cmd`, start it with `start.cmd` from the
repository root, or:

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui\app.py
```

The functional Configure, Review, detached-run, and Results flow includes
Overview, Market detail, Data explorer, Technical details, and Downloads.

The accepted static reference screens remain available from a separate
developer entry point. They are not the product workflow.

## Requirements

Python 3.13 or newer. On this machine use the existing isolated interpreter,
not Anaconda base:

```text
C:\Users\Epyon\AppData\Local\Temp\stepinbel-py313\Scripts\python.exe
```

Do not install packages into Anaconda unless the user explicitly asks.

## Launch

From the StepInBel project root:

```powershell
$python = 'C:\Users\Epyon\AppData\Local\Temp\stepinbel-py313\Scripts\python.exe'
& $python -m streamlit run ui\app.py
```

Developer-only static foundation preview:

```powershell
& $python -m streamlit run ui\foundation_app.py
```

## Tests

From the StepInBel project root:

```powershell
$python = 'C:\Users\Epyon\AppData\Local\Temp\stepinbel-py313\Scripts\python.exe'
& $python -m pytest ui\tests -q
```
