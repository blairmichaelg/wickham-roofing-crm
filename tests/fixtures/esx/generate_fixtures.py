"""
Script to create synthetic non-proprietary test fixtures for Xactimate ESX parser tests.
"""
from pathlib import Path
import zipfile

FIXTURES_DIR = Path(__file__).parent


def create_fixtures():
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    xml_8d = """<?xml version="1.0" encoding="utf-8"?>
<!-- SYNTHETIC NON-PROPRIETARY TEST FIXTURE FOR WICKHAM ROOFING CRM ESX PARSER -->
<XACTIMATE_ESTIMATE>
  <PROJECT_INFO>
    <CLAIM_NUMBER>ESX-SYNTH-8D-2026</CLAIM_NUMBER>
    <INSURER_NAME>Georgia Farm &amp; Casualty</INSURER_NAME>
    <POLICY_NUMBER>POL-GA-88412</POLICY_NUMBER>
    <ESTIMATE_ID>EST-8D-001</ESTIMATE_ID>
    <PROFILE>8D</PROFILE>
    <PROFILE_DESCRIPTION>Contractor Profile with O&amp;P</PROFILE_DESCRIPTION>
    <PRICE_LIST>GAAT8X_MAY24</PRICE_LIST>
  </PROJECT_INFO>
  <ROOF_GEOMETRY>
    <PITCH>7/12</PITCH>
    <TOTAL_SQUARES>32.00</TOTAL_SQUARES>
    <EAVES_LF>140.00</EAVES_LF>
    <VALLEYS_LF>35.00</VALLEYS_LF>
    <RAKES_LF>85.00</RAKES_LF>
  </ROOF_GEOMETRY>
  <EMBEDDED_PL>
    <PL_CODE>GAAT8X_MAY24</PL_CODE>
    <ITEMS_COUNT>4</ITEMS_COUNT>
  </EMBEDDED_PL>
  <LINE_ITEMS>
    <LINE_ITEM>
      <CAT>RFG</CAT>
      <ACT>&amp;</ACT>
      <DESC>Tear off comp shingles - 3 tab</DESC>
      <UNIT>SQ</UNIT>
      <QTY>32.00</QTY>
      <UNIT_PRICE>55.00</UNIT_PRICE>
      <TAX>0.00</TAX>
      <RCV>1760.00</RCV>
      <DEPRECIATION>0.00</DEPRECIATION>
      <ACV>1760.00</ACV>
    </LINE_ITEM>
    <LINE_ITEM>
      <CAT>RFG</CAT>
      <ACT>&amp;</ACT>
      <DESC>Laminated shingle roofing - architectural</DESC>
      <UNIT>SQ</UNIT>
      <QTY>35.00</QTY>
      <UNIT_PRICE>225.00</UNIT_PRICE>
      <TAX>0.00</TAX>
      <RCV>7875.00</RCV>
      <DEPRECIATION>787.50</DEPRECIATION>
      <ACV>7087.50</ACV>
    </LINE_ITEM>
    <LINE_ITEM>
      <CAT>RFG</CAT>
      <ACT>+</ACT>
      <DESC>Ridge cap - composition shingles</DESC>
      <UNIT>LF</UNIT>
      <QTY>85.00</QTY>
      <UNIT_PRICE>6.20</UNIT_PRICE>
      <TAX>0.00</TAX>
      <RCV>527.00</RCV>
      <DEPRECIATION>52.70</DEPRECIATION>
      <ACV>474.30</ACV>
    </LINE_ITEM>
    <LINE_ITEM>
      <CAT>RFG</CAT>
      <ACT>+</ACT>
      <DESC>Drip edge / gutter apron - white</DESC>
      <UNIT>LF</UNIT>
      <QTY>140.00</QTY>
      <UNIT_PRICE>3.10</UNIT_PRICE>
      <TAX>0.00</TAX>
      <RCV>434.00</RCV>
      <DEPRECIATION>43.40</DEPRECIATION>
      <ACV>390.60</ACV>
    </LINE_ITEM>
  </LINE_ITEMS>
  <TOTALS>
    <GROSS_RCV>10596.00</GROSS_RCV>
    <TOTAL_DEPRECIATION>883.60</TOTAL_DEPRECIATION>
    <DEDUCTIBLE>1000.00</DEDUCTIBLE>
    <NET_CLAIM>8712.40</NET_CLAIM>
  </TOTALS>
</XACTIMATE_ESTIMATE>"""

    xml_5l = xml_8d.replace(
        "<PROFILE>8D</PROFILE>", "<PROFILE>5L</PROFILE>"
    ).replace(
        "<CLAIM_NUMBER>ESX-SYNTH-8D-2026</CLAIM_NUMBER>",
        "<CLAIM_NUMBER>ESX-SYNTH-5L-2026</CLAIM_NUMBER>",
    )

    xml_mismatched = xml_8d.replace(
        "<GROSS_RCV>10596.00</GROSS_RCV>",
        "<GROSS_RCV>14000.00</GROSS_RCV>",
    ).replace(
        "<NET_CLAIM>8712.40</NET_CLAIM>",
        "<NET_CLAIM>12116.40</NET_CLAIM>",
    )

    with zipfile.ZipFile(FIXTURES_DIR / "realistic_contractor_8d.esx", "w") as zf:
        zf.writestr("estimate.xml", xml_8d.encode("utf-8"))

    with zipfile.ZipFile(FIXTURES_DIR / "realistic_carrier_5l.esx", "w") as zf:
        zf.writestr("estimate.xml", xml_5l.encode("utf-8"))

    with zipfile.ZipFile(FIXTURES_DIR / "mismatched_totals.esx", "w") as zf:
        zf.writestr("estimate.xml", xml_mismatched.encode("utf-8"))


if __name__ == "__main__":
    create_fixtures()
    print("Fixtures generated successfully.")
