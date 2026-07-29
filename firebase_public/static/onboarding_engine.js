function getCompanyInfo() {
    const sel = document.getElementById('quoteCompanySelect');
    let val = sel ? sel.value : "DONINGTON VALE (Medical)";
    
    let industry = "medical";
    let name = "Donington Vale";
    
    if (val.includes("SECURITY")) {
        industry = "security";
        name = "Dolo Security Services";
    } else if (val.includes("CONSTRUCTION")) {
        industry = "construction";
        name = "Apex Construction";
    }
    return { name, industry };
}

function getOnboardingMessageHTML(companyName, industry) {
    let text = `Good evening. I have vetted <strong>${companyName}</strong> and fully reviewed your compliance vault. `;
    let links = "";
    
    if (industry === "security") {
        text += `Based on your profile as a Security company, I have compiled a list of specialized accreditations to boost your odds of winning upcoming physical and cybersecurity tenders.`;
        links = `
            <div style="margin-top: 10px; font-size: 12px;">
                <strong>Recommended Portals:</strong><br/>
                <a href="https://www.psira.co.za/" target="_blank" style="color: #c5a880; text-decoration: none;">\u2192 PSIRA (Private Security Industry Regulatory Authority)</a><br/>
                <a href="https://www.sasseta.org.za/" target="_blank" style="color: #c5a880; text-decoration: none;">\u2192 SASSETA (Safety and Security SETA)</a>
            </div>
        `;
    } else if (industry === "construction") {
        text += `Based on your profile as a Construction company, I have compiled a list of specialized accreditations to boost your odds of winning upcoming public works and infrastructure tenders.`;
        links = `
            <div style="margin-top: 10px; font-size: 12px;">
                <strong>Recommended Portals:</strong><br/>
                <a href="https://www.cidb.org.za/" target="_blank" style="color: #c5a880; text-decoration: none;">\u2192 CIDB (Construction Industry Development Board)</a><br/>
                <a href="https://www.nhbrc.org.za/" target="_blank" style="color: #c5a880; text-decoration: none;">\u2192 NHBRC (National Home Builders Registration Council)</a>
            </div>
        `;
    } else {
        text += `Based on your profile as a Medical/Health company, I have compiled a list of specialized accreditations to boost your odds of winning upcoming healthcare supply tenders.`;
        links = `
            <div style="margin-top: 10px; font-size: 12px;">
                <strong>Recommended Portals:</strong><br/>
                <a href="https://www.sahpra.org.za/" target="_blank" style="color: #c5a880; text-decoration: none;">\u2192 SAHPRA (South African Health Products Regulatory Authority)</a><br/>
                <a href="https://www.sabs.co.za/" target="_blank" style="color: #c5a880; text-decoration: none;">\u2192 SABS (South African Bureau of Standards - ISO 13485)</a>
            </div>
        `;
    }
    
    return `<div class="msg-bubble">${text}${links}</div>`;
}

function generateRoadmapPDF() {
    const { name, industry } = getCompanyInfo();
    const { jsPDF } = window.jspdf;
    const doc = new jsPDF();
    
    // Header
    doc.setFillColor(30, 30, 30);
    doc.rect(0, 0, 210, 40, 'F');
    doc.setTextColor(197, 168, 128); // DOLO Gold
    doc.setFont("helvetica", "bold");
    doc.setFontSize(22);
    doc.text(name.toUpperCase(), 105, 20, null, null, "center");
    doc.setFontSize(12);
    doc.setTextColor(255, 255, 255);
    doc.setFont("helvetica", "normal");
    doc.text("Compliance & Accreditation Roadmap", 105, 30, null, null, "center");
    
    // Content
    doc.setTextColor(40, 40, 40);
    doc.setFontSize(14);
    doc.setFont("helvetica", "bold");
    doc.text("1. Current Audit Status", 20, 55);
    
    doc.setFontSize(11);
    doc.setFont("helvetica", "normal");
    doc.text(`We have reviewed the ${name} vault. Your CSD and B-BBEE Level are valid.`, 20, 65);
    doc.text("However, to maximize your win-rate for upcoming high-value tenders, ", 20, 72);
    doc.text("we strongly recommend acquiring the following specialized accreditations:", 20, 79);
    
    // Recommendations
    doc.setFont("helvetica", "bold");
    doc.text("2. Recommended Accreditations", 20, 95);
    doc.setFont("helvetica", "normal");
    
    if (industry === "security") {
        // Item 1
        doc.setFillColor(245, 245, 245);
        doc.rect(20, 105, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("PSIRA Certificate (Private Security Regulatory)", 25, 113);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Mandatory for ALL security tenders. Estimated timeline: 2-4 weeks.", 25, 120);
        
        // Item 2
        doc.setFontSize(11);
        doc.rect(20, 130, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("SASSETA Registration", 25, 138);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Required to supply trained guards and personnel. Essential for national contracts.", 25, 145);
        
        // Item 3
        doc.setFontSize(11);
        doc.rect(20, 155, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("ISO 27001 (Information Security Management)", 25, 163);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Crucial for cybersecurity and access control bids.", 25, 170);
        
    } else if (industry === "construction") {
        // Item 1
        doc.setFillColor(245, 245, 245);
        doc.rect(20, 105, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("CIDB Grade 4 (or higher)", 25, 113);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Unlocks public works tenders above R4 million. Your financials support this upgrade.", 25, 120);
        
        // Item 2
        doc.setFontSize(11);
        doc.rect(20, 130, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("NHBRC Registration", 25, 138);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Mandatory for any housing or residential building contracts. Estimated 30 days.", 25, 145);
        
        // Item 3
        doc.setFontSize(11);
        doc.rect(20, 155, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("COIDA Letter of Good Standing", 25, 163);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Mandatory for all labor-intensive construction contracts. Required immediately.", 25, 170);
        
    } else {
        // Medical / General
        doc.setFillColor(245, 245, 245);
        doc.rect(20, 105, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("SAHPRA Medical Device License", 25, 113);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Mandatory for supplying medical equipment or devices to state hospitals.", 25, 120);
        
        // Item 2
        doc.setFontSize(11);
        doc.rect(20, 130, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("ISO 13485 (Medical Devices QMS)", 25, 138);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Globally recognized standard for medical device manufacturing and supply.", 25, 145);
        
        // Item 3
        doc.setFontSize(11);
        doc.rect(20, 155, 170, 20, 'F');
        doc.setFont("helvetica", "bold");
        doc.text("ISO 9001:2015 (Quality Management)", 25, 163);
        doc.setFont("helvetica", "normal");
        doc.setFontSize(10);
        doc.text("Required for 65% of Tier-1 Government Contracts. Estimated timeline: 3 months.", 25, 170);
    }
    
    // Footer
    doc.setFontSize(9);
    doc.setTextColor(150, 150, 150);
    doc.text("Generated by DOLO AI Agent \u2014 Strictly Confidential", 105, 280, null, null, "center");
    
    doc.save(`${name.replace(/ /g, "_")}_Accreditation_Roadmap.pdf`);
}
