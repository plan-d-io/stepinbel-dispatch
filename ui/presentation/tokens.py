"""Semantic design tokens for the StepInBel Streamlit interface."""

from __future__ import annotations

from typing import Literal

StepStatus = Literal["complete", "current", "unlocked", "unavailable"]
WidthVariant = Literal["form", "wide"]
StatusTone = Literal["success", "warning", "danger", "info"]

STEPS: tuple[str, ...] = (
    "Configure",
    "Review & run",
    "Results",
)

RESULT_TABS: tuple[str, ...] = (
    "Overview",
    "Market detail",
    "Data explorer",
    "Technical details",
    "Downloads",
)

APP_NAME = "StepInBel"
APP_TITLE = "PHS market dispatch"
DEMO_MODE_LABEL = "Demo mode"
RUN_IN_PROGRESS_LABEL = "Run in progress"
DEMO_MODE_HELP = "Demo mode opens a saved model run result on 2025 market data."
MARKETS_INDEPENDENT_COPY = "Selected markets are independent runs. Revenues are not additive."
REVIEW_FINAL_HEADING = "Final check"
REVIEW_READY_TITLE = "Ready to run"
REVIEW_READY_BODY = (
    "Your settings are valid, and market data are available for the full selected period."
)
CONFIGURE_TITLE = "PHS dispatch simulator"
CONFIGURE_SUBTITLE = (
    "Simulate how a pumped-hydro storage asset could have performed in Belgium’s "
    "day-ahead, mFRR and aFRR markets using historical market data."
)
CONFIGURE_SECTION = "Configure the simulation"
PERIOD_SELECTOR_LABEL = "Simulation period"
PERIOD_START_LABEL = "Simulation period start"
PERIOD_END_LABEL = "Simulation period end"
PERIOD_2025 = "2025 — complete year for all markets"
PERIOD_2026 = "2026 — incomplete; available dates depend on the selected markets"
PERIOD_CUSTOM = "Custom period"
PERIOD_OPTIONS: tuple[str, ...] = (PERIOD_2025, PERIOD_2026, PERIOD_CUSTOM)
PERIOD_CUSTOM_HELP = "Periods before 2025 support Day-ahead only."
SOLVER_LOG_COPY = "Show detailed messages from the HiGHS optimisation solver in the run log."
GRID_HELP_AUTOMATIC = (
    "Suggested from the larger pump or turbine rating. This value updates until you edit it."
)
GRID_HELP_OVERRIDE = "Custom grid limit. Suggested value: 1.000 MW."
GRID_RESET_LABEL = "Use suggested limit"
HOW_TO_READ_TITLE = "How to read these results"
COLUMN_GLOSSARY_TITLE = "What do these columns mean?"
SIMULTANEOUS_DIAGNOSTIC = (
    "Quarter-hours in which the continuous LP pumps and generates at the same time. "
    "This is a modelling diagnostic, not extra revenue or participation in several markets. "
    "If the physical plant cannot operate in both directions at once, the result may be an upper-bound estimate."
)
ABOUT_SIMULTANEOUS_TITLE = "About simultaneous operation"
SIMULTANEOUS_ENERGY_NET_NOTE = (
    "The energy-net value is revenue occurring during those intervals, "
    "not incremental revenue created by simultaneity."
)
ABOUT_SIMULTANEOUS_BODY = (
    "These are quarter-hours in which the continuous LP pumps and generates at the same time. "
    "This is a modelling diagnostic, not extra revenue or participation in several markets. "
    "The energy-net value is revenue occurring during those intervals, not incremental revenue "
    "created by simultaneity. If the physical plant cannot operate in both directions "
    "simultaneously, the continuous result may represent an upper bound."
)
RESULTS_TABLE_CAPTION = (
    "Values come from the completed simulation. Display rounding may affect visible sums."
)
RESERVED_TAB_BODY = "This section will be added in the next step."
TECHNICAL_ERROR_TITLE = "Technical details unavailable"
TECHNICAL_ERROR_BODY = "The stored technical information is incomplete or incompatible."
DOWNLOADS_ERROR_TITLE = "Downloads unavailable"
DOWNLOADS_ERROR_BODY = "The stored result files are incomplete or incompatible."
DOWNLOADS_ZIP_TOO_LARGE = (
    "The complete result package is larger than the display limit. "
    "Individual files remain available."
)
DOWNLOADS_FORMAT_HELP_TITLE = "Which format should I use?"
DOWNLOADS_FORMAT_HELP = (
    "CSV is convenient for spreadsheets and general inspection. "
    "Parquet is compact and preserves data types for analytical tools. "
    "The report is the readable run summary. "
    "The complete result package contains the complete stored result record."
)
DOWNLOADS_PACKAGE_LABEL = "Download complete result package"
DOWNLOADS_SUMMARY_LABEL = "Download result summary"
DOWNLOADS_REPORT_LABEL = "Download report"
DOWNLOADS_SELECTED_LABEL = "Download selected file"
TECHNICAL_CONFIGURED_HEADING = "Configured simulation"
TECHNICAL_ADVANCED_HEADING = "Advanced configuration"
TECHNICAL_SOURCES_HEADING = "Data sources and verification"
TECHNICAL_SOLVER_LABEL = "HiGHS solver"
TECHNICAL_CHECKS_HEADING = "Solution checks"
TECHNICAL_CONFIG_RECORD = "Configuration record"
TECHNICAL_CHECKS_HELP_TITLE = "About these checks"
TECHNICAL_CHECKS_HELP = (
    "These are numerical consistency checks stored with the optimisation result. "
    "They are not a new simulation or an independent verification performed here."
)
TECHNICAL_EVENTS_LIMIT_NOTE = "Showing the first {count} of {total} stored events. The complete file remains available under Downloads."
TECHNICAL_TEXT_TRUNCATED = "Display is truncated. The complete file remains available under Downloads."
RUN_TYPE_LIVE = "Live simulation"
RUN_TYPE_DEMO = "Saved demonstration"
SCOPE_OVERALL_LABEL = "Overall comparison"
COMPLETE_PACKAGE_HEADING = "Complete result package"
DOWNLOADS_STORED_INTRO = "The output results can also be found at:"
HIGHEST_REVENUE_TITLE = "Highest simulated total site revenue"
HIGHEST_REVENUE_NOTE = "Highest simulated revenue"
DAY_AHEAD_CAPACITY_COPY = "Capacity payments do not apply to Day-ahead."
PV_NOT_INCLUDED_COPY = "PV was not included in this simulation."
RESULTS_ERROR_TITLE = "Results could not be opened"
RESULTS_ERROR_BODY = "The stored result files are incomplete or incompatible."
EXPLORER_ERROR_TITLE = "Data explorer could not be opened"
EXPLORER_ERROR_BODY = (
    "The stored dispatch data for this selection are incomplete or incompatible."
)
EXPLORER_EXPANDER_TITLE = "Stored dispatch week"
EXPLORER_WEEK_INTRO = (
    "Charts show the stored quarter-hour dispatch in Belgian local time. "
    "Changing the market or week does not rerun the simulation."
)
EXPLORER_INTERVAL_CAPTION = (
    "Total interval revenue includes net energy revenue and direct PV-export revenue. "
    "Capacity revenue is settled by block and is not included in this chart."
)
EXPLORER_CAPACITY_BOTH_CAPTION = (
    "Upward and downward commitments are shown as non-negative committed capacity."
)
EXPLORER_DA_CAPACITY_COPY = "Balancing capacity commitments do not apply to Day-ahead."
EXPLORER_NO_CAPACITY_COPY = "No balancing-capacity commitments were stored for this week."
EXPLORER_X_TITLE = "Belgian local time"
EXPLORER_ZOOM_CAPTION = (
    "Drag horizontally to zoom all panels. Double-click any panel to reset the complete week."
)
EXPLORER_GROUP_MAIN = "Main results"
EXPLORER_GROUP_GRID = "Grid connection loading"
EXPLORER_GROUP_CAPACITY = "Balancing capacity commitments"
EXPLORER_PANEL_CAPACITY = "Committed capacity"
RESULTS_SUBTITLE_MULTI = "Dedicated-market runs for one asset and simulation period."
RESULTS_SUBTITLE_ONE = "Operational results for one dedicated-market run."
PV_HELP_OFF = (
    "Optional. Co-located PV can supply pumping, be exported within the grid limit, or be curtailed."
)
PV_HELP_ON = (
    "Uses the Belgian reference PV profile. PV can supply pumping, be exported within the grid "
    "limit, or be curtailed."
)
STEPINBEL_LOGO_NAME = "STEPinBEL-logo2.png"
STEPINBEL_LOGO_WIDTH_PX = 213
STEPINBEL_LOGO_HEIGHT_PX = 80
FOOTER_RULE_GAP_PX = 16
FPS_LOGO_NAME = "LOGO-economie-SPF.png"
FPS_LOGO_WIDTH_PX = 493
FPS_LOGO_HEIGHT_PX = 218
FPS_FOOTER_DISPLAY_WIDTH_PX = 104
FPS_FOOTER_DISPLAY_HEIGHT_PX = 46
ENERGENT_LOGO_NAME = "Energent.png"
ENERGENT_FOOTER_DISPLAY_WIDTH_PX = 72
ENERGENT_FOOTER_DISPLAY_HEIGHT_PX = 46
FOOTER_FPS_CAPTION = "This project is supported by the FPS Economy"
FOOTER_ENERGENT_CAPTION = "Made by Energent"

PRIMARY = "#009898"
PRIMARY_HOVER = "#007a7a"
PRIMARY_FOCUS = "#009898"

PAGE_BG = "#f4f6f7"
SURFACE = "#ffffff"
SURFACE_MUTED = "#eef1f3"

TEXT = "#1b1d21"
TEXT_SECONDARY = "#5c6570"
TEXT_MUTED = "#7a828c"

BORDER = "#d6dbe1"

SUCCESS = "#1f7a4d"
SUCCESS_BG = "#e7f5ee"
WARNING = "#9a6700"
WARNING_BG = "#fff6e5"
DANGER = "#b42318"
DANGER_BG = "#fdecea"
INFO = "#175cd3"
INFO_BG = "#eff4ff"

SPACE_XS = 4
SPACE_SM = 8
SPACE_MD = 12
SPACE_LG = 16
SPACE_XL = 20
SPACE_XXL = 28

RADIUS_PX = 6
FORM_WIDTH_PX = 1120
WIDE_WIDTH_PX = 1120
NARROW_BREAKPOINT_PX = 900
TAP_MIN_PX = 36
LOGO_DISPLAY_PX = STEPINBEL_LOGO_WIDTH_PX

TABULAR_NUMS = "tabular-nums"

CHART_AFRR = PRIMARY
CHART_DA = TEXT
CHART_MFRR = SUCCESS
CHART_PUMP = PRIMARY
CHART_TURBINE = "#c45c26"
CHART_PV = "#c9892b"
CHART_PV_EXPORT = SUCCESS
CHART_PV_CURTAIL = TEXT_SECONDARY
CHART_RESERVOIR = "#2a6f97"
CHART_PRICE_SELL = SUCCESS
CHART_PRICE_BUY = DANGER
CHART_REVENUE = "#1b6b93"
CHART_GRID = TEXT_SECONDARY
CHART_LIMIT = BORDER
CHART_PAPER = SURFACE
CHART_AXIS = TEXT_SECONDARY
