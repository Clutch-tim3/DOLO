import os
from fpdf import FPDF
from docx import Document

fixtures_dir = "tests/fixtures"
os.makedirs(fixtures_dir, exist_ok=True)

# 1. Alfred Duma (Fails functionality & locality)
alfred_duma_text = """
ALFRED DUMA LOCAL MUNICIPALITY
TENDER NO: DF 04/2026
Tender value: R 2,500,000
Bid amount: R 2,000,000
Tender Year: 2026
Procedure type: Request for Proposal
Supply type: Services
Briefing session: 12 August 2026
Closing date: 24 August 2026
EVALUATION CRITERIA: Functionality
The minimum qualifying score of 80% is required to be eligible for preference points.
Past contract track record: Max points 30
Locality: Must be registered in Alfred Duma municipality.
Preference point system: 80/20
Specific goals: 5 points HDI, 15 points Locality.
"""
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 10, alfred_duma_text)
pdf.output(f"{fixtures_dir}/alfred_duma.pdf")

# 2. LV Cabling (Eligible, High Value, Open)
lv_cabling_text = """
DEPARTMENT OF PUBLIC WORKS
REQUEST FOR PROPOSAL
TENDER NO: DPW 100/2026
Tender Year: 2026
Project Estimate: R 60,000,000
Bid amount: R 55,000,000
Closing Date: 30 November 2026
This tender will be evaluated on the 90/10 system.
Supply type: Works
No compulsory briefing.
"""
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 10, lv_cabling_text)
pdf.output(f"{fixtures_dir}/lv_cabling_tender.pdf")

# 3. Comms Campaign DOCX (Eligible, Medium Value, RFQ)
comms_text = """
NATIONAL COMMUNICATIONS AGENCY 
REQUEST FOR QUOTATION 
RFB_001_COMMS 
Tender Year: 2026 
Budget: R 15,000,000 
Bid amount: R 14,000,000 
Deadline: 15 October 2026 
System for this tender is 80/20. 
Supply type: Services 
"""
doc = Document()
doc.add_paragraph(comms_text)
doc.save(f"{fixtures_dir}/rfb_001_comms.docx")

# 4. Eligible Sample (No functionality, no locality)
eligible_text = """
STANDARD PROCUREMENT TENDER
TENDER NO: STD-2026
Tender Value: R 1,000,000
Closing date: 20 September 2026
Applicable preference point system 80/20.
Boilerplate: up to R50,000,000
"""
pdf = FPDF()
pdf.add_page()
pdf.set_font("Arial", size=12)
pdf.multi_cell(0, 10, eligible_text)
pdf.output(f"{fixtures_dir}/eligible_sample.pdf")

# 5. Malformed PDF
with open(f"{fixtures_dir}/malformed.pdf", "w") as f:
    f.write("This is a corrupt PDF file that lacks the proper PDF headers.")

print("Fixtures generated successfully.")
