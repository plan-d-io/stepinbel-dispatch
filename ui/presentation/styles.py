"""Scoped Streamlit CSS. Token values are interpolated from ui.presentation.tokens."""

from __future__ import annotations

import streamlit as st

from ui.presentation import tokens as t


def stylesheet() -> str:
    """Return the StepInBel stylesheet. Selectors stay scoped to the app."""
    return f"""
:root, .stApp {{
  --primary-color: {t.PRIMARY};
}}
.stApp {{
  background: {t.PAGE_BG};
  color: {t.TEXT};
}}
.stApp .block-container {{
  padding-top: 3rem;
  padding-bottom: 2rem;
  max-width: 100%;
}}
.stApp [data-testid="stBaseButton-primary"] {{
  background-color: {t.PRIMARY};
  border-color: {t.PRIMARY};
  color: {t.SURFACE};
}}
.stApp [data-testid="stBaseButton-primary"]:hover:not(:disabled) {{
  background-color: {t.PRIMARY_HOVER};
  border-color: {t.PRIMARY_HOVER};
}}
.stApp [data-testid="stBaseButton-primary"]:focus-visible {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.stApp [data-testid="stBaseButton-primary"]:disabled {{
  opacity: 0.55;
}}
.stApp [data-testid="stBaseButton-primary"],
.stApp [data-testid="stBaseButton-secondary"],
.stApp [data-testid="stBaseButton-tertiary"] {{
  min-height: {t.TAP_MIN_PX}px;
  font-weight: 600;
}}
.stApp [data-testid="stRadio"] input,
.stApp [data-testid="stCheckbox"] input {{
  accent-color: {t.PRIMARY};
}}
.stApp [data-testid="stRadio"] input:focus-visible,
.stApp [data-testid="stCheckbox"] input:focus-visible {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.stApp [data-testid="stTextInput"]:focus-within,
.stApp [data-testid="stNumberInput"]:focus-within,
.stApp [data-testid="stSelectbox"]:focus-within,
.stApp [data-testid="stTextArea"]:focus-within {{
  outline: 2px solid {t.PRIMARY_FOCUS};
  outline-offset: 2px;
}}
.stApp [data-testid="stMetricValue"] {{
  font-variant-numeric: {t.TABULAR_NUMS};
}}
.stApp [data-testid="stDataFrame"] {{
  font-variant-numeric: {t.TABULAR_NUMS};
}}
.st-key-sib-shell,
.st-key-sib-chrome {{
  display: flex !important;
  flex-direction: column !important;
  align-items: stretch;
  box-sizing: border-box;
  width: 100%;
}}
.st-key-sib-body-form,
.st-key-sib-body-wide,
.st-key-sib-dev-preview {{
  display: block;
  box-sizing: border-box;
  width: 100%;
}}
.st-key-sib-identity-row {{
  display: block;
  width: 100%;
  flex: 0 0 auto !important;
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-sib-body-wide,
.st-key-sib-dev-preview {{
  max-width: {t.WIDE_WIDTH_PX}px;
  margin-left: auto;
  margin-right: auto;
}}
.st-key-sib-body-form {{
  max-width: {t.FORM_WIDTH_PX}px;
  margin-left: auto;
  margin-right: auto;
}}
.st-key-sib-dev-preview {{
  margin-bottom: {t.SPACE_LG}px;
  padding: {t.SPACE_MD}px {t.SPACE_LG}px;
  border: 1px dashed {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  background: {t.SURFACE_MUTED};
}}
.st-key-sib-chart-frame,
.st-key-sib-table-frame {{
  border: 1px solid {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  background: {t.SURFACE};
  padding: {t.SPACE_MD}px;
}}
.st-key-sib-metrics [data-testid="stMetricValue"],
.st-key-sib-metrics [data-testid="stMetricValue"] *,
[class*="st-key-sib-metrics"] [data-testid="stMetricValue"],
[class*="st-key-sib-metrics"] [data-testid="stMetricValue"] * {{
  font-variant-numeric: {t.TABULAR_NUMS};
  font-size: 1.05rem;
  font-weight: 600;
  line-height: 1.3;
  white-space: normal !important;
  overflow: visible !important;
  overflow-wrap: break-word;
}}
.st-key-sib-metrics [data-testid="stMetric"],
[class*="st-key-sib-metrics"] [data-testid="stMetric"] {{
  min-width: 0;
}}
.sib-identity {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: {t.SPACE_SM}px;
  margin-bottom: {t.SPACE_SM}px;
}}
.sib-identity-stack {{
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}}
.sib-identity-name {{
  display: block;
  font-size: 1.05rem;
  font-weight: 700;
  line-height: 1.25;
  color: {t.TEXT};
}}
.st-key-sib-identity-row img {{
  width: {t.STEPINBEL_LOGO_WIDTH_PX}px;
  height: auto;
  max-width: 100%;
}}
.sib-identity-versions {{
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: 0.75rem;
  font-weight: 400;
  line-height: 1.3;
  color: {t.TEXT_MUTED};
}}
.sib-identity-version {{
  display: block;
  font-size: inherit;
  font-weight: inherit;
  color: inherit;
  line-height: inherit;
}}
.sib-identity-status {{
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: {t.SPACE_SM}px;
}}
.st-key-sib-action-row {{
  margin-top: {t.SPACE_MD}px;
}}
.st-key-sib-action-row .sib-continue-reason,
.sib-continue-reason {{
  margin: 0 0 {t.SPACE_SM}px;
  text-align: right;
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
}}
.sib-quiet-status {{
  display: inline-flex;
  align-items: center;
  gap: {t.SPACE_XS}px;
  font-size: 0.85rem;
  font-weight: 500;
  color: {t.TEXT_SECONDARY};
  pointer-events: none;
}}
.sib-quiet-dot {{
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  background: {t.PRIMARY};
  flex-shrink: 0;
}}
.sib-quiet-dot-running {{
  background: {t.WARNING};
}}
.sib-pill {{
  display: inline-flex;
  align-items: center;
  min-height: {t.TAP_MIN_PX}px;
  padding: 0 {t.SPACE_MD}px;
  border: 1px solid {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  font-size: 0.85rem;
  background: {t.SURFACE};
  color: {t.TEXT};
}}
.sib-pill-active {{
  border-color: {t.PRIMARY};
  border-width: 2px;
  font-weight: 600;
}}
.sib-tabs {{
  display: flex;
  flex-wrap: wrap;
  gap: {t.SPACE_SM}px;
  margin: {t.SPACE_MD}px 0;
}}
.st-key-sib-result-tabs {{
  display: block;
  width: 100%;
  margin: {t.SPACE_MD}px 0;
}}
.st-key-sib-result-tabs [data-testid="stHorizontalBlock"] {{
  display: flex !important;
  flex-wrap: wrap !important;
  gap: {t.SPACE_SM}px !important;
  align-items: stretch !important;
}}
[class*="st-key-sib-tab-"] [data-testid="stBaseButton-secondary"],
[class*="st-key-sib-tab-"] [data-testid="stBaseButton-primary"] {{
  border-radius: {t.RADIUS_PX}px;
  min-height: {t.TAP_MIN_PX}px;
  padding: 0 {t.SPACE_MD}px;
  font-size: 0.85rem;
  white-space: nowrap;
  font-variant-numeric: {t.TABULAR_NUMS};
}}
[class*="st-key-sib-tab-active-"] [data-testid="stBaseButton-primary"] {{
  border-color: {t.PRIMARY};
  font-weight: 600;
}}
.sib-table-numeric {{
  font-variant-numeric: {t.TABULAR_NUMS};
}}
.st-key-sib-stepper {{
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: wrap !important;
  justify-content: flex-start !important;
  align-items: center !important;
  gap: {t.SPACE_SM}px !important;
  width: 100%;
  flex: 0 0 auto !important;
}}
.st-key-sib-stepper[data-testid="stHorizontalBlock"],
.st-key-sib-stepper [data-testid="stHorizontalBlock"] {{
  display: flex !important;
  flex-direction: row !important;
  flex-wrap: wrap !important;
  justify-content: flex-start !important;
  align-items: center !important;
  gap: {t.SPACE_SM}px !important;
  width: 100%;
}}
[class*="st-key-sib-step-"] {{
  flex: 0 0 auto !important;
  width: auto !important;
  max-width: max-content !important;
  min-width: fit-content;
}}
[class*="st-key-sib-step-"] [data-testid="stBaseButton-secondary"] {{
  border-radius: {t.RADIUS_PX}px;
  min-height: {t.TAP_MIN_PX}px;
  padding: 0 {t.SPACE_MD}px;
  font-size: 0.85rem;
  font-weight: 500;
  white-space: nowrap;
}}
[class*="st-key-sib-step-current-"] [data-testid="stBaseButton-secondary"]:disabled {{
  opacity: 1;
  border-color: {t.PRIMARY};
  border-width: 2px;
  color: {t.TEXT};
  font-weight: 600;
  background: {t.SURFACE};
  pointer-events: none;
}}
[class*="st-key-sib-step-complete-"] [data-testid="stBaseButton-secondary"] {{
  border-color: {t.PRIMARY};
  color: {t.TEXT};
}}
[class*="st-key-sib-step-unlocked-"] [data-testid="stBaseButton-secondary"] {{
  border-color: {t.BORDER};
  background: {t.SURFACE};
  color: {t.TEXT_SECONDARY};
}}
[class*="st-key-sib-step-unavailable-"] [data-testid="stBaseButton-secondary"] {{
  border-style: dashed;
  background: {t.SURFACE_MUTED};
  color: {t.TEXT_MUTED};
}}
.sib-rule {{
  height: 1px;
  background: {t.BORDER};
  margin: 0;
  border: 0;
}}
.sib-kicker {{
  display: block;
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: {t.TEXT_MUTED};
  line-height: 1.3;
  margin: 0;
  padding-bottom: {t.SPACE_SM}px;
}}
.st-key-sib-page-header {{
  display: block;
  width: 100%;
  margin-bottom: {t.SPACE_MD}px;
}}
.sib-page-lead {{
  display: block;
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  line-height: 1.4;
  margin: 0 0 {t.SPACE_MD}px;
  padding-bottom: {t.SPACE_SM}px;
}}
.sib-section-lead {{
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  line-height: 1.45;
  margin: 0 0 {t.SPACE_MD}px;
  padding-bottom: {t.SPACE_XS}px;
  overflow: visible;
}}
.st-key-sib-body-form [data-testid="stHeading"],
.st-key-sib-body-wide [data-testid="stHeading"] {{
  margin-bottom: {t.SPACE_SM}px;
}}
.st-key-sib-body-form [data-testid="stTable"],
.st-key-sib-body-wide [data-testid="stTable"] {{
  overflow: visible !important;
  margin-bottom: {t.SPACE_MD}px;
}}
.st-key-sib-body-form [data-testid="stTable"] table,
.st-key-sib-body-wide [data-testid="stTable"] table {{
  width: 100%;
  table-layout: auto;
  background: {t.SURFACE};
}}
.st-key-sib-body-form [data-testid="stTable"] th,
.st-key-sib-body-wide [data-testid="stTable"] th {{
  background: {t.SURFACE_MUTED} !important;
  color: {t.TEXT};
}}
.st-key-sib-body-form [data-testid="stTable"] td,
.st-key-sib-body-wide [data-testid="stTable"] td {{
  background: {t.SURFACE} !important;
  color: {t.TEXT};
  font-variant-numeric: {t.TABULAR_NUMS};
}}
.st-key-sib-body-form [data-testid="stTable"] th,
.st-key-sib-body-form [data-testid="stTable"] td,
.st-key-sib-body-wide [data-testid="stTable"] th,
.st-key-sib-body-wide [data-testid="stTable"] td {{
  white-space: normal !important;
  overflow-wrap: anywhere;
  word-break: break-word;
}}
.sib-axis-label {{
  color: {t.TEXT_SECONDARY};
  font-size: 0.9rem;
  margin: 0 0 {t.SPACE_SM}px;
}}
.sib-caption {{
  color: {t.TEXT_MUTED};
  font-size: 0.85rem;
  margin-top: {t.SPACE_SM}px;
}}
.sib-progress {{
  width: 100%;
  height: 8px;
  background: {t.SURFACE_MUTED};
  border: 1px solid {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  overflow: hidden;
}}
.sib-progress-fill {{
  height: 8px;
  background: {t.PRIMARY};
}}
.sib-highlight {{
  border: 1px solid {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  background: {t.SURFACE};
  padding: {t.SPACE_MD}px;
  height: 100%;
}}
.sib-highlight-title {{
  font-weight: 600;
  margin: 0 0 {t.SPACE_XS}px;
  color: {t.TEXT};
}}
.sib-highlight-total {{
  font-variant-numeric: {t.TABULAR_NUMS};
  font-size: 1.15rem;
  font-weight: 600;
  margin: 0;
  color: {t.TEXT};
}}
.sib-highlight-note {{
  color: {t.TEXT_MUTED};
  font-size: 0.8rem;
  margin: {t.SPACE_SM}px 0 0;
}}
.sib-config-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(15.5rem, 1fr));
  gap: {t.SPACE_MD}px;
  width: 100%;
  box-sizing: border-box;
}}
.sib-config-card {{
  border: 1px solid {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  background: {t.SURFACE};
  padding: {t.SPACE_MD}px;
  min-width: 0;
}}
.sib-config-card-title {{
  font-weight: 600;
  margin: 0 0 {t.SPACE_SM}px;
  color: {t.TEXT};
}}
.sib-config-row {{
  display: flex;
  justify-content: space-between;
  gap: {t.SPACE_SM}px {t.SPACE_MD}px;
  flex-wrap: wrap;
  margin: 0 0 {t.SPACE_XS}px;
}}
.sib-config-row:last-child {{
  margin-bottom: 0;
}}
.sib-readout {{
  min-width: 0;
}}
.sib-readout-label {{
  display: block;
  color: {t.TEXT_MUTED};
  font-size: 0.78rem;
  margin: 0;
}}
.sib-readout-value {{
  display: block;
  color: {t.TEXT};
  font-variant-numeric: {t.TABULAR_NUMS};
  font-weight: 600;
  margin: 0;
}}
.sib-status-summary {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(7.5rem, 1fr));
  gap: {t.SPACE_MD}px {t.SPACE_LG}px;
  width: 100%;
  box-sizing: border-box;
}}
.sib-status-item {{
  min-width: 0;
}}
.sib-status-label {{
  display: block;
  color: {t.TEXT_MUTED};
  font-size: 0.78rem;
  line-height: 1.3;
  margin: 0 0 2px;
}}
.sib-status-value {{
  display: block;
  color: {t.TEXT};
  font-size: 1.1rem;
  font-weight: 600;
  line-height: 1.3;
  font-variant-numeric: {t.TABULAR_NUMS};
  margin: 0;
  overflow-wrap: anywhere;
}}
.st-key-app-footer {{
  display: block;
  box-sizing: border-box;
  width: 100%;
  max-width: {t.WIDE_WIDTH_PX}px;
  margin-top: {t.SPACE_XXL}px;
  margin-left: auto;
  margin-right: auto;
  padding-top: {t.SPACE_MD}px;
  padding-bottom: {t.SPACE_MD}px;
}}
.sib-footer {{
  display: block;
  box-sizing: border-box;
  width: 100%;
}}
.sib-footer-rule-wrap {{
  display: block;
  box-sizing: border-box;
  width: 100%;
  padding-bottom: {t.FOOTER_RULE_GAP_PX}px;
}}
.sib-footer-rule {{
  display: block;
  box-sizing: border-box;
  width: 100%;
  height: 1px;
  margin: 0;
  border: 0;
  background: {t.BORDER};
}}
.sib-footer-row {{
  display: grid;
  grid-template-columns: 1fr 1fr;
  column-gap: {t.SPACE_LG}px;
  align-items: start;
}}
.sib-footer-block {{
  display: flex;
  flex-direction: column;
  min-width: 0;
}}
.sib-footer-block-fps {{
  justify-self: start;
  align-items: flex-start;
  text-align: left;
}}
.sib-footer-block-energent {{
  justify-self: end;
  align-items: flex-end;
  text-align: right;
}}
.sib-footer-logo-box {{
  height: {t.FPS_FOOTER_DISPLAY_HEIGHT_PX}px;
  display: flex;
  align-items: center;
}}
.sib-footer-logo-box-fps {{
  justify-content: flex-start;
}}
.sib-footer-logo-box-energent {{
  justify-content: flex-end;
}}
.sib-footer-logo {{
  display: block;
  max-width: 100%;
  object-fit: contain;
}}
.sib-footer-logo-fps {{
  width: {t.FPS_FOOTER_DISPLAY_WIDTH_PX}px;
  height: {t.FPS_FOOTER_DISPLAY_HEIGHT_PX}px;
}}
.sib-footer-logo-energent {{
  width: {t.ENERGENT_FOOTER_DISPLAY_WIDTH_PX}px;
  height: {t.ENERGENT_FOOTER_DISPLAY_HEIGHT_PX}px;
}}
.sib-footer-caption {{
  margin: {t.SPACE_SM}px 0 0;
  font-size: 0.85rem;
  line-height: 1.4;
  color: {t.TEXT_MUTED};
}}
.sib-footer-caption-fps {{
  text-align: left;
}}
.sib-footer-caption-energent {{
  text-align: right;
}}
.sib-glossary {{
  margin: 0;
}}
.sib-glossary-row {{
  display: grid;
  grid-template-columns: minmax(10rem, 14rem) 1fr;
  column-gap: {t.SPACE_MD}px;
  row-gap: 2px;
  padding: {t.SPACE_SM}px 0;
  border-bottom: 1px solid {t.BORDER};
  align-items: start;
}}
.sib-glossary-row:last-child {{
  border-bottom: 0;
}}
.sib-glossary dt {{
  margin: 0;
  font-weight: 700;
  color: {t.TEXT};
}}
.sib-glossary dd {{
  margin: 0;
  color: {t.TEXT_SECONDARY};
}}
.sib-empty {{
  border: 1px dashed {t.BORDER};
  border-radius: {t.RADIUS_PX}px;
  padding: {t.SPACE_LG}px;
  color: {t.TEXT_SECONDARY};
  background: {t.SURFACE};
}}
@media (max-width: {t.NARROW_BREAKPOINT_PX}px) {{
  .st-key-sib-stepper,
  .st-key-sib-stepper[data-testid="stHorizontalBlock"],
  .st-key-sib-stepper [data-testid="stHorizontalBlock"] {{
    flex-wrap: wrap !important;
  }}
  .sib-identity {{
    align-items: flex-start;
  }}
  .st-key-sib-metrics [data-testid="stHorizontalBlock"],
  [class*="st-key-sib-metrics"] [data-testid="stHorizontalBlock"] {{
    flex-wrap: wrap;
  }}
  .st-key-sib-metrics [data-testid="stColumn"],
  [class*="st-key-sib-metrics"] [data-testid="stColumn"] {{
    min-width: min(100%, 220px);
    flex: 1 1 220px;
  }}
  .st-key-sib-shell {{
    overflow-x: hidden;
  }}
  .st-key-app-footer {{
    max-width: 100%;
  }}
  .st-key-sib-identity-row img {{
    width: min({t.STEPINBEL_LOGO_WIDTH_PX}px, 100%);
  }}
  .sib-footer-row {{
    grid-template-columns: 1fr;
    row-gap: {t.SPACE_LG}px;
  }}
  .sib-footer-block-fps,
  .sib-footer-block-energent {{
    justify-self: start;
    align-items: flex-start;
    text-align: left;
  }}
  .sib-footer-logo-box-energent {{
    justify-content: flex-start;
  }}
  .sib-footer-caption-energent {{
    text-align: left;
  }}
  .sib-footer-logo {{
    width: auto;
    max-width: 100%;
    height: auto;
    max-height: {t.FPS_FOOTER_DISPLAY_HEIGHT_PX}px;
  }}
  .sib-glossary-row {{
    grid-template-columns: 1fr;
  }}
}}
""".strip()


def inject_styles() -> None:
    """Inject scoped CSS once into the current page."""
    st.markdown(f"<style>\n{stylesheet()}\n</style>", unsafe_allow_html=True)
