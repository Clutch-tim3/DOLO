/**
 * DOLO Agent Chatbot Engine
 * A client-side intelligent chatbot that works on Firebase static hosting
 * without requiring a backend API. Uses company profile context from the vault.
 */

const AGENT_KNOWLEDGE = {
    security: {
        industry: "Private Security",
        specialties: ["Physical Security", "Access Control", "Cybersecurity", "Risk Assessment", "CCTV & Surveillance"],
        accreditations: [
            { name: "PSIRA", full: "Private Security Industry Regulatory Authority", url: "https://www.psira.co.za/", required: true },
            { name: "SASSETA", full: "Safety and Security SETA", url: "https://www.sasseta.org.za/", required: true },
            { name: "ISO 27001", full: "Information Security Management", url: "https://www.iso.org/isoiec-27001-information-security.html", required: false },
            { name: "COIDA", full: "Compensation for Occupational Injuries and Diseases Act", url: "https://www.labour.gov.za/", required: true }
        ],
        tenderTips: [
            "Always highlight your PSIRA grading prominently in your tender response.",
            "Include proof of trained, vetted personnel with firearm competency certificates where applicable.",
            "Demonstrate 24/7 control room capabilities and incident response protocols.",
            "B-BBEE Level 1 status gives you maximum procurement recognition (135%)."
        ]
    },
    construction: {
        industry: "Construction & Infrastructure",
        specialties: ["Civil Engineering", "Building Construction", "Road Works", "Water & Sanitation", "Electrical Infrastructure"],
        accreditations: [
            { name: "CIDB", full: "Construction Industry Development Board", url: "https://www.cidb.org.za/", required: true },
            { name: "NHBRC", full: "National Home Builders Registration Council", url: "https://www.nhbrc.org.za/", required: true },
            { name: "COIDA", full: "Letter of Good Standing", url: "https://www.labour.gov.za/", required: true },
            { name: "ISO 9001", full: "Quality Management System", url: "https://www.iso.org/iso-9001-quality-management.html", required: false }
        ],
        tenderTips: [
            "Your CIDB grading determines the maximum contract value you can bid on. Grade 4 unlocks tenders above R4 million.",
            "Include a detailed project methodology and programme (Gantt chart) with your submission.",
            "Always attach your health and safety plan as per the Construction Regulations 2014.",
            "Demonstrate plant and equipment ownership or hire agreements for the specific project scope."
        ]
    },
    medical: {
        industry: "Medical & Health Supplies",
        specialties: ["Medical Devices", "Pharmaceutical Supply", "Laboratory Equipment", "Hospital Consumables", "Healthcare IT"],
        accreditations: [
            { name: "SAHPRA", full: "South African Health Products Regulatory Authority", url: "https://www.sahpra.org.za/", required: true },
            { name: "ISO 13485", full: "Medical Devices Quality Management", url: "https://www.iso.org/iso-13485-medical-devices.html", required: true },
            { name: "SABS", full: "South African Bureau of Standards", url: "https://www.sabs.co.za/", required: false },
            { name: "ISO 9001", full: "Quality Management System", url: "https://www.iso.org/iso-9001-quality-management.html", required: false }
        ],
        tenderTips: [
            "SAHPRA licensing is mandatory for any medical device or pharmaceutical supply tender.",
            "Include product datasheets, CE marking, and regulatory approvals with every submission.",
            "Demonstrate cold-chain logistics capabilities if bidding on pharmaceutical or vaccine tenders.",
            "Reference your ISO 13485 certification to show compliance with international medical device standards."
        ]
    }
};

// Chat history for context
let chatHistory = [];
let notificationCount = 0;

function getAgentResponse(userMessage, companyName, industry) {
    const msg = userMessage.toLowerCase().trim();
    const kb = AGENT_KNOWLEDGE[industry] || AGENT_KNOWLEDGE.medical;
    
    // Track conversation
    chatHistory.push({ role: 'user', content: userMessage });
    
    let response = "";
    
    // --- Accreditation queries ---
    if (msg.includes("accreditation") || msg.includes("certification") || msg.includes("license") || msg.includes("psira") || msg.includes("cidb") || msg.includes("sahpra") || msg.includes("iso") || msg.includes("coida") || msg.includes("sasseta") || msg.includes("nhbrc") || msg.includes("sabs")) {
        const accList = kb.accreditations.map(a => {
            const status = a.required ? '<span style="color:#e05c5c;">MANDATORY</span>' : '<span style="color:#4CAF50;">RECOMMENDED</span>';
            return `<li style="margin-bottom:8px;"><strong>${a.name}</strong> — ${a.full} ${status}<br/><a href="${a.url}" target="_blank" style="color:#c5a880; font-size:11px;">${a.url}</a></li>`;
        }).join("");
        response = `Based on your ${kb.industry} profile, here are the relevant accreditations for <strong>${companyName}</strong>:<ul style="margin-top:8px; padding-left:18px;">${accList}</ul>`;
    }
    // --- Tender tips ---
    else if (msg.includes("tip") || msg.includes("advice") || msg.includes("how to win") || msg.includes("improve") || msg.includes("odds") || msg.includes("chances") || msg.includes("winning")) {
        const tips = kb.tenderTips.map((t, i) => `<li style="margin-bottom:6px;">${t}</li>`).join("");
        response = `Here are strategic tips to improve <strong>${companyName}</strong>'s tender win rate:<ol style="margin-top:8px; padding-left:18px;">${tips}</ol>`;
    }
    // --- Company profile ---
    else if (msg.includes("company") || msg.includes("profile") || msg.includes("who am i") || msg.includes("my business") || msg.includes("about us") || msg.includes("what do we do") || msg.includes("what does")) {
        response = `<strong>${companyName}</strong> is registered as a <strong>${kb.industry}</strong> company. Your core specializations include: ${kb.specialties.join(", ")}. You hold a B-BBEE Level 1 contributor status, which provides maximum procurement recognition (135%) under the Preferential Procurement Policy Framework Act.`;
    }
    // --- B-BBEE ---
    else if (msg.includes("bbbee") || msg.includes("b-bbee") || msg.includes("bee") || msg.includes("broad-based")) {
        response = `Your B-BBEE status is currently <strong>Level 1</strong> (135% procurement recognition). This is the highest possible level and significantly boosts your competitive advantage in government tenders. To maintain this, ensure annual verification through a SANAS-accredited verification agency. <a href="https://www.thedtic.gov.za/financial-and-non-financial-support/b-bbee/b-bbee-codes-of-good-practice/" target="_blank" style="color:#c5a880;">View B-BBEE Codes of Good Practice →</a>`;
    }
    // --- CSD ---
    else if (msg.includes("csd") || msg.includes("central supplier") || msg.includes("supplier database")) {
        response = `Your CSD (Central Supplier Database) registration should be active and verified. CSD registration is <strong>mandatory</strong> for all government tenders in South Africa. Ensure your tax clearance, company registration, and bank details are up to date on the portal. <a href="https://secure.csd.gov.za/" target="_blank" style="color:#c5a880;">Access CSD Portal →</a>`;
    }
    // --- Tender process ---
    else if (msg.includes("tender") || msg.includes("bid") || msg.includes("rfq") || msg.includes("rfp") || msg.includes("procurement")) {
        response = `For ${kb.industry} tenders, here's the standard process:<ol style="margin-top:8px; padding-left:18px;">
            <li style="margin-bottom:4px;">Monitor eTender Portal and sector-specific portals daily</li>
            <li style="margin-bottom:4px;">Download the bid documents and check mandatory requirements</li>
            <li style="margin-bottom:4px;">Attend compulsory briefing sessions (if required)</li>
            <li style="margin-bottom:4px;">Prepare your response using the prescribed forms (SBD 1-9)</li>
            <li style="margin-bottom:4px;">Include all returnable documents (tax clearance, B-BBEE, CSD, ${kb.accreditations[0].name})</li>
            <li style="margin-bottom:4px;">Submit before the closing date/time — late submissions are disqualified</li>
        </ol><a href="https://www.etenders.gov.za/" target="_blank" style="color:#c5a880;">Visit eTender Portal →</a>`;
    }
    // --- Vault / Documents ---
    else if (msg.includes("vault") || msg.includes("document") || msg.includes("upload") || msg.includes("compliance") || msg.includes("archive")) {
        response = `Your compliance vault currently holds your CSD Report (Valid) and B-BBEE Certificate (Valid). I recommend uploading the following additional documents to strengthen your tender submissions: <ul style="margin-top:8px; padding-left:18px;">
            <li>Tax Clearance Certificate (TCC)</li>
            <li>${kb.accreditations[0].name} Registration Certificate</li>
            <li>COIDA Letter of Good Standing</li>
            <li>Company Registration (CIPC)</li>
            <li>Directors' ID Documents</li>
        </ul>You can upload these in the <strong>Vault</strong> tab.`;
    }
    // --- Quotation ---
    else if (msg.includes("quote") || msg.includes("quotation") || msg.includes("pricing") || msg.includes("price")) {
        response = `You can generate a professional quotation using the <strong>Generate Quotation</strong> tool in the console. Upload the tender PDF, select your company profile, and the system will extract line items and produce a formatted PDF quotation with your company branding. You have <strong>5 quotes per day</strong> on the Starter plan.`;
    }
    // --- Calendar ---
    else if (msg.includes("calendar") || msg.includes("deadline") || msg.includes("due") || msg.includes("schedule") || msg.includes("submission date")) {
        response = `Check the <strong>Calendar & Deadlines</strong> tab to view all upcoming tender submission dates. I recommend setting reminders at least 5 business days before each closing date to allow time for final review and physical delivery if required.`;
    }
    // --- Greetings ---
    else if (msg.match(/^(hi|hello|hey|good morning|good afternoon|good evening|howzit|sup|yo)[\s!?.]*$/i)) {
        const hour = new Date().getHours();
        const greeting = hour < 12 ? "Good morning" : hour < 17 ? "Good afternoon" : "Good evening";
        response = `${greeting}. I'm your DOLO Agent — ready to assist with anything related to <strong>${companyName}</strong>. You can ask me about your accreditations, tender strategy, company compliance, or anything else. How can I help you today?`;
    }
    // --- Help ---
    else if (msg.includes("help") || msg.includes("what can you do") || msg.includes("capabilities") || msg.includes("features")) {
        response = `I can assist you with:<ul style="margin-top:8px; padding-left:18px;">
            <li><strong>Company Profile</strong> — View your registration details, B-BBEE level, and specializations</li>
            <li><strong>Accreditations</strong> — Get recommendations specific to ${kb.industry} with direct application links</li>
            <li><strong>Tender Strategy</strong> — Tips to improve your win rate on government and private tenders</li>
            <li><strong>Compliance Vault</strong> — Check which documents are valid and what's missing</li>
            <li><strong>Quotation Generation</strong> — Create professional PDF quotations from tender documents</li>
            <li><strong>Calendar & Deadlines</strong> — Track upcoming tender submission dates</li>
        </ul>Just ask me anything — no restrictions!`;
    }
    // --- Thank you ---
    else if (msg.includes("thank") || msg.includes("thanks") || msg.includes("cheers") || msg.includes("appreciate")) {
        response = `You're welcome. I'm here whenever you need assistance with ${companyName}'s tender operations. Don't hesitate to ask anything.`;
    }
    // --- General fallback (intelligent, not restrictive) ---
    else {
        response = `I understand your query regarding "${userMessage}". While I specialize in tender management, compliance, and quotation generation for <strong>${companyName}</strong> (${kb.industry}), I'm happy to assist with any question you have. Could you provide a bit more context so I can give you the most relevant guidance? You can also try asking about:<ul style="margin-top:8px; padding-left:18px;">
            <li>Your company accreditations and how to obtain them</li>
            <li>Tender submission tips and strategy</li>
            <li>B-BBEE status and compliance requirements</li>
            <li>Document vault and what's needed for submissions</li>
        </ul>`;
    }
    
    chatHistory.push({ role: 'agent', content: response });
    return response;
}

function incrementNotification() {
    notificationCount++;
    const badge = document.getElementById('agentNotifBadge');
    if (badge) {
        badge.textContent = notificationCount;
        badge.style.display = 'inline-flex';
    }
}

function clearNotifications() {
    notificationCount = 0;
    const badge = document.getElementById('agentNotifBadge');
    if (badge) {
        badge.style.display = 'none';
    }
}
