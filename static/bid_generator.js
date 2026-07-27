function generateComplexBidResponse(doc, companyName) {
    const cliveRed = [200, 51, 31];
    
    // Page 1: Cover
    doc.setFillColor(...cliveRed);
    doc.rect(0, 0, 210, 40, 'F');
    doc.setTextColor(255, 255, 255);
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(20);
    doc.text('NATIONAL HEALTH LABORATORY SERVICE', 105, 20, null, null, 'center');
    doc.setFontSize(14);
    doc.text('INVITATION FOR BID - RESPONSE DOCUMENT', 105, 30, null, null, 'center');
    
    doc.setTextColor(40, 40, 40);
    doc.setFontSize(12);
    doc.text('BID NUMBER: RFB029/26/27', 105, 60, null, null, 'center');
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(10);
    doc.text('DESCRIPTION: Outright Purchase of an Automated Ampoule Filling and Sealing Machine', 105, 75, null, null, 'center');
    doc.text('including Service and Maintenance for a Period of Five (5) Years for SAVP', 105, 82, null, null, 'center');
    
    doc.setFont('helvetica', 'bold');
    doc.text('CLOSING DATE: 21 August 2026 | VALIDITY PERIOD: 180 Days', 105, 100, null, null, 'center');
    
    doc.setFillColor(245, 230, 230);
    doc.rect(15, 115, 180, 10, 'F');
    doc.setTextColor(...cliveRed);
    doc.text('CONFIDENTIAL - PROPRIETARY DOCUMENT', 105, 122, null, null, 'center');
    
    // Part A Table
    doc.autoTable({
        startY: 135,
        head: [['PART A: SUPPLIER INFORMATION', '']],
        body: [
            ['NAME OF BIDDER', companyName],
            ['POSTAL ADDRESS', 'PO Box 12456, Randburg, 2125'],
            ['STREET ADDRESS', 'Building 7, Pharma Park, 45 Modderfontein Road'],
            ['TELEPHONE NUMBER', '011 555 0142'],
            ['E-MAIL ADDRESS', 'tenders@' + companyName.toLowerCase().replace(/ /g, '') + '.co.za'],
            ['VAT REGISTRATION NUMBER', '4120256789'],
            ['TCS PIN / CSD No', 'CSD: 1234567890 / TCS PIN: TCS-2026-78945612'],
            ['B-BBEE STATUS LEVEL', 'Level 2 Contributor (125% Procurement Recognition)'],
            ['COMPANY REGISTRATION', '2015/123456/07'],
            ['TYPE OF COMPANY', '(Pty) Limited']
        ],
        theme: 'grid',
        headStyles: { fillColor: cliveRed, textColor: 255 },
        styles: { fontSize: 9, cellPadding: 4 },
        columnStyles: { 0: { fontStyle: 'bold', cellWidth: 70 } }
    });
    
    let finalY = doc.lastAutoTable.finalY + 20;
    doc.setFont('helvetica', 'bold');
    doc.setTextColor(40,40,40);
    doc.text('TOTAL BID PRICE (ALL INCLUSIVE): R 18,947,500.00 (VAT Incl.)', 15, finalY);
    
    // Page 2: Compliance
    doc.addPage();
    doc.text('ADMINISTRATIVE COMPLIANCE - MANDATORY RETURNABLE DOCUMENTS', 15, 20);
    doc.autoTable({
        startY: 25,
        head: [['No.', 'Description', 'Comply', 'Do Not Comply']],
        body: [
            ['1', 'Proof of Attendance of Compulsory Briefing session.', '[X] YES', '[ ] NO'],
            ['2', 'The Service Providers have to agree with Special Conditions.', '[X] YES', '[ ] NO'],
            ['3', 'The Service Providers have to agree with NHLS General Conditions.', '[X] YES', '[ ] NO'],
            ['4', 'Fully completed and Signed Bidder\\'s Disclosure SBD4.', '[X] YES', '[ ] NO']
        ],
        theme: 'grid',
        headStyles: { fillColor: cliveRed },
        styles: { fontSize: 9 },
        columnStyles: { 0: { cellWidth: 15 }, 2: { cellWidth: 30, textColor: [0,128,0], fontStyle: 'bold' }, 3: { cellWidth: 30 } }
    });
    
    finalY = doc.lastAutoTable.finalY + 15;
    doc.text('ESSENTIAL RETURNABLE DOCUMENTS', 15, finalY);
    doc.autoTable({
        startY: finalY + 5,
        head: [['No.', 'Description', 'Comply', 'Do Not Comply']],
        body: [
            ['1', 'Submission of original valid Tax Clearance Certificate or Tax PIN.', '[X] YES', '[ ] NO'],
            ['2', 'Preferential Procurement Claim form and copy of B-BBEE Cert.', '[X] YES', '[ ] NO'],
            ['3', 'Audited financial statements not older than two (2) years.', '[X] YES', '[ ] NO'],
            ['4', 'Proof of Central Supplier Database (CSD) Registration.', '[X] YES', '[ ] NO'],
            ['5', 'Manufacturer/supplier certified by ISO 3834-2021.', '[X] YES', '[ ] NO'],
            ['6', 'Letter of Good Standing from Dept of Employment (COIDA).', '[X] YES', '[ ] NO'],
            ['7', 'Manufacturer/supplier certified by ISO 9001:2015.', '[X] YES', '[ ] NO']
        ],
        theme: 'grid',
        headStyles: { fillColor: cliveRed },
        styles: { fontSize: 9 },
        columnStyles: { 0: { cellWidth: 15 }, 2: { cellWidth: 30, textColor: [0,128,0], fontStyle: 'bold' }, 3: { cellWidth: 30 } }
    });
    
    // Page 3: Technical Specs
    doc.addPage();
    doc.text('ANNEXURE A: TECHNICAL SPECIFICATION COMPLIANCE', 15, 20);
    doc.autoTable({
        startY: 25,
        head: [['Ref', 'Description / Specification', 'Comply', 'Do Not Comply']],
        body: [
            ['a', 'Type of Packing: Glass closed ampoules (Schott)', '[X] YES', '[ ] NO'],
            ['b', 'Intermediate Bulk Tank: 5 Litres', '[X] YES', '[ ] NO'],
            ['c', 'Type of ampoules: Schott closed ampoules', '[X] YES', '[ ] NO'],
            ['d', 'HMI/Control panel: Located outside the LAF/RABS', '[X] YES', '[ ] NO'],
            ['e', 'Laminar Air Flow (LAF) with RABS and gloves.', '[X] YES', '[ ] NO'],
            ['f', 'Glove Testing Instrument - able to test integrity.', '[X] YES', '[ ] NO'],
            ['g', 'Number of filling Heads / Needles: Three (3)', '[X] YES', '[ ] NO'],
            ['h', 'In-feed Peristaltic pump: 1 pump', '[X] YES', '[ ] NO'],
            ['i', 'Piston Pumps: Three (3) piston pumps', '[X] YES', '[ ] NO'],
            ['j', 'Integrated Particle counter/monitoring system', '[X] YES', '[ ] NO'],
            ['k', '3x Passive, 3x Active air sampling stations', '[X] YES', '[ ] NO'],
            ['o', 'Production Output: > 20-25 ampoules/min (Offer: 30)', '[X] YES', '[ ] NO'],
            ['p', 'Filling Accuracy: ± 1%', '[X] YES', '[ ] NO'],
            ['q', 'Required Gas for Sealing: Acetylene and Compressed air', '[X] YES', '[ ] NO'],
            ['s', 'Noise Control: Must not exceed 80 decibels', '[X] YES', '[ ] NO'],
            ['z', 'Overall Dimension: 8.3m(L) × 6.6m(W) × 2.45m(H)', '[X] YES', '[ ] NO'],
            ['aa', 'Gross Weight: Must not exceed 6,000kg', '[X] YES', '[ ] NO']
        ],
        theme: 'grid',
        headStyles: { fillColor: cliveRed },
        styles: { fontSize: 9 },
        columnStyles: { 0: { cellWidth: 15 }, 2: { cellWidth: 25, textColor: [0,128,0], fontStyle: 'bold' }, 3: { cellWidth: 25 } }
    });
    
    // Page 4: Pricing Schedule
    doc.addPage();
    doc.text('ANNEXURE B: PRICING SCHEDULE', 15, 20);
    doc.autoTable({
        startY: 25,
        head: [['Description', 'Qty', 'Year 1', 'Year 2', 'Year 3', 'Year 4', 'Year 5', 'Total (ZAR)']],
        body: [
            ['Outright Purchase: Automated Ampoule Filling Machine', '1', 'R 9,500,000', '-', '-', '-', '-', 'R 9,500,000'],
            ['Installation & Commissioning', '1', 'R 850,000', '-', '-', '-', '-', 'R 850,000'],
            ['Programming & Configuration', '1', 'R 320,000', '-', '-', '-', '-', 'R 320,000'],
            ['Service & Maintenance', '1', 'R 1,140,000', 'R 1,197,000', 'R 1,256,850', 'R 1,319,692', 'R 1,385,677', 'R 6,299,219'],
            ['Training for 3 Managers', '1', 'R 85,000', '-', '-', '-', '-', 'R 85,000'],
            [{ content: 'SUBTOTAL (VAT EXCL.)', colSpan: 7, styles: { halign: 'right', fontStyle: 'bold' } }, 'R 17,054,219.63'],
            [{ content: 'VAT @ 15%', colSpan: 7, styles: { halign: 'right', fontStyle: 'bold' } }, 'R 2,558,132.94'],
            [{ content: 'TOTAL PRICE (VAT INCL.)', colSpan: 7, styles: { halign: 'right', fontStyle: 'bold', fillColor: [240,240,240] } }, { content: 'R 19,612,352.57', styles: { fontStyle: 'bold', fillColor: [240,240,240] } }]
        ],
        theme: 'grid',
        headStyles: { fillColor: cliveRed },
        styles: { fontSize: 8 },
        columnStyles: { 0: { cellWidth: 50 } }
    });
    
    finalY = doc.lastAutoTable.finalY + 15;
    doc.setFillColor(255, 245, 230); // light gold/yellow alert
    doc.rect(15, finalY, 180, 15, 'F');
    doc.setTextColor(40,40,40);
    doc.setFont('helvetica', 'bold');
    doc.text('ADJUSTED TOTAL BID PRICE (ALL INCLUSIVE): R 18,947,500.00', 20, finalY + 6);
    doc.setFont('helvetica', 'italic');
    doc.setFontSize(8);
    doc.text('Note: A special discount of R 664,852.57 has been applied for NHLS, resulting in the final tendered amount.', 20, finalY + 11);
    
    // Page 5: SBD 6.1 and Signatures
    doc.addPage();
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(12);
    doc.text('ANNEXURE D: PREFERENTIAL PROCUREMENT CLAIM FORM (SBD 6.1)', 15, 20);
    doc.autoTable({
        startY: 25,
        head: [['Specific Goal', 'Points Allocated (80/20)', 'Points Claimed']],
        body: [
            ['51% Owned by black people', '4', '4'],
            ['51% Owned by Black people who are women', '4', '4'],
            ['51% owned by Black people with disabilities', '2', '0'],
            ['51% Owned by Black people who are youth', '4', '2'],
            ['51% Owned by black people living in rural areas', '2', '2'],
            ['Exempted Micro Enterprise (EME) / QSE', '4', '4'],
            [{ content: 'TOTAL POINTS CLAIMED', colSpan: 2, styles: { halign: 'right', fontStyle: 'bold' } }, { content: '16', styles: { fontStyle: 'bold' } }]
        ],
        theme: 'grid',
        headStyles: { fillColor: cliveRed },
        styles: { fontSize: 9 }
    });
    
    finalY = doc.lastAutoTable.finalY + 20;
    doc.setFont('helvetica', 'bold');
    doc.text('DECLARATION AND SIGNATURE', 15, finalY);
    doc.setFont('helvetica', 'normal');
    doc.setFontSize(9);
    doc.text('I, the undersigned, certify that the information furnished in this bid response is true and correct.', 15, finalY + 8);
    
    doc.text('SIGNATURE: _______________________________', 15, finalY + 25);
    doc.text('DATE: ___________________', 120, finalY + 25);
    doc.text('NAME: Johnathan M. Peters', 15, finalY + 35);
    doc.text('DESIGNATION: Managing Director', 120, finalY + 35);
    
    doc.setFont('helvetica', 'bold');
    doc.setFontSize(8);
    doc.setTextColor(150,150,150);
    doc.text(companyName.toUpperCase() + ' - GENERATED VIA DOLO AI (PROTOTYPE BUILD)', 105, 280, null, null, 'center');
    
    return doc;
}
