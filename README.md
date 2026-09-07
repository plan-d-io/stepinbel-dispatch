# StepInBel

<p align="left">
  <a href="https://energent.be/"><img src="ui/assets/STEPinBEL-logo2.png" alt="Energent" width="180"></a>
</p>

StepInBel estimates how a pumped-hydro storage (PHS) asset could have performed
on Belgian electricity markets. For a selected period and asset configuration,
it simulates three independent dedicated-market cases:

- day-ahead;
- mFRR;
- aFRR.

Each selected market is a separate simulation. Revenues from different markets
are not additive and do not represent simultaneous participation in more than
one market.

The results are historical simulations under the configured assumptions. They
are not forecasts, operating schedules, or a complete investment case.

The application runs locally on your computer. Live simulation outputs are
written under `outputs/`. The production solver is HiGHS.

## Quick start on Windows

First install **64-bit Python 3.13**. The Microsoft Store build is a suitable
source.

Then:

1. Clone the [stepinbel-dispatch](https://github.com/plan-d-io/stepinbel-dispatch) repository,
   or download and extract its ZIP (upper right corner of this page > `Code` > `Download ZIP`).
2. Place the folder somewhere you can write files.
3. Double-click `setup.cmd`. It creates a private `.venv` in that folder and
   installs the required dependencies. This can take several minutes.
4. Double-click `start.cmd`.
5. Open the local address shown in the terminal if the browser does not open
   automatically.

After the first setup, start the application with `start.cmd`.

If Python 3.13 is installed in a custom location, set `STEPINBEL_PYTHON` to the
full path of `python.exe` before running `setup.cmd`.

## Try the saved demonstration

Enable **Demo mode** on the first page to walk through a completed 2025
comparison without starting a new simulation. Demo mode does not run HiGHS.

## Run your own simulation

Choose the markets, Belgian delivery period, pumped-hydro asset, optional
co-located PV, and grid connection. Review the resolved settings, then start
the run. Progress stays visible while the worker is active.

Completed live results are stored under `outputs/<run-id>/`.

## Installation check

Run the installation check at any time with:

```powershell
.\.venv\Scripts\python.exe scripts\doctor.py
```

## Command-line use

Activate the environment from PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Then inspect the supported commands:

```powershell
stepinbel --help
```

Direct CLI runs require `--data-dir` pointing at the bundled `data/` folder.
See [Command line](docs/CLI.md).

Further documentation:

- [Scope](docs/SCOPE.md)
- [Behavioural authority](docs/METHODOLOGY.md)
- [Linear program](docs/MODEL.md)
- [Run artifacts](docs/ARTIFACTS.md)
- [Dedicated-market comparison](docs/COMPARISON.md)

## Development

Install the test extra and run the public test suites:

```powershell
.\.venv\Scripts\python.exe -m pip install "pytest>=8"
.\.venv\Scripts\python.exe -m pytest tests ui\tests -q
```

## Licence

This project is distributed under the
[PolyForm Noncommercial License 1.0.0](LICENSE.md). Noncommercial use,
modification, and distribution are permitted under those terms. Commercial use
requires separate permission from the licensor.

## Made by

StepInBel is an Energent project. Made by Joannes Laveyne of
[Plan-D.io](https://www.plan-d.io/), for [Energent cvba](https://energent.be/).
The project is supported by the FPS Economy. See [AUTHORS.md](AUTHORS.md).

<p align="right">
  <img src="ui/assets/LOGO-economie-SPF.png" alt="FPS Economy" height="46">
</p>
