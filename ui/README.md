# Streamlit interface

`ui/app.py` is the supported Streamlit entry point. After `setup.cmd`, start
the application with `start.cmd`, or from the repository root:

```powershell
.\.venv\Scripts\python.exe -m streamlit run ui\app.py
.\.venv\Scripts\python.exe -m pytest ui\tests -q
```

The interface presents public StepInBel workflows. It does not implement market
physics, the optimization model, or artifact validation.

The simulator version is stored in `src/stepinbel/VERSION`. The independent
front-end version is stored in `ui/VERSION`.

Project authorship is recorded in [`AUTHORS.md`](../AUTHORS.md).

Saved demonstration artifacts live under `ui/demo_artifacts/`. Public validation
checks them before the UI opens them. Do not replace them with unreviewed run
folders.
