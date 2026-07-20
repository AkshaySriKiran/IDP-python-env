const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');

function sanitizeVal(val) {
  if (val === null || val === undefined) return "NA";
  const s = String(val).trim();
  if (s === "" || s.toLowerCase() === "null" || s.toLowerCase() === "undefined" || s.toLowerCase() === "na") return "NA";
  return s;
}

function isCleanMaintenanceRow(row) {
  const comp = sanitizeVal(row.subsystem_component);
  if (comp === "NA") return false;
  const checks = sanitizeVal(row.checks_instructions);
  if (checks === "NA") return false;
  return true;
}

function isolateComponent(sentence) {
  const lowerS = sentence.toLowerCase();
  const partClasses = [
    { key: "lubrication", terms: ["oil lubrication system", "main grease lubrication", "lubrication system", "lubrication", "lubricating oil", "oil circuit", "oil pressure"] },
    { key: "brake_system", terms: ["hydraulic disc brake system", "disc brake system", "disc brake", "brake system"] },
    { key: "pneumatic", terms: ["pneumatic control system", "pneumatic system", "air release port"] },
    { key: "bearing", terms: ["drum shaft bearing", "support bearing", "bearing block", "main bearing", "ball bearings", "bearing"] },
    { key: "filter", terms: ["lubricating oil filter element", "oil suction filter element", "filter element", "filter core", "suction filter", "filter"] },
    { key: "caliper", terms: ["safety caliper", "brake caliper", "caliper weld seams", "caliper"] },
    { key: "disc", terms: ["brake disc assembly", "disc spring set", "disc spring", "brake disc", "disc"] },
    { key: "coupling", terms: ["input shaft coupling", "drum gear coupling", "gear coupling", "coupling"] },
    { key: "valve", terms: ["pneumatic directional valve", "toggle valve", "relief valve", "breather valve", "directional valve", "valve"] },
    { key: "seal", terms: ["safety caliper seal assembly", "oil seal", "seal kit", "seal assembly", "mechanical seal", "shaft seal", "seal"] },
    { key: "roller", terms: ["kick back roller", "kickback roller", "roller"] },
    { key: "pump", terms: ["lubricating oil pump", "gear oil pump", "pump motor set", "oil pump", "pump"] },
    { key: "motor", terms: ["main motor", "oil pump motor set", "motor winding", "motor"] },
    { key: "gearbox", terms: ["gearbox oil sump", "reduction gearbox", "gearbox", "reducer"] },
    { key: "drum", terms: ["lebus drum", "drawworks drum", "drum shaft assembly", "drum shaft", "drum"] },
    { key: "rope", terms: ["steel wire rope", "wire rope clamp", "rope stopper", "rope holder", "wire rope", "rope"] },
    { key: "plates", terms: ["transitional plates", "drum side plate", "friction plate", "plates", "plate"] },
    { key: "sleeve", terms: ["shaft sleeve", "sleeve"] },
    { key: "gasket", terms: ["flat gasket", "casing gasket", "gasket"] },
    { key: "o_ring", terms: ["o-ring", "seal ring"] },
    { key: "joint", terms: ["mechanical joint", "flexible coupling", "joint"] },
    { key: "shaft", terms: ["transmission shaft", "drive shaft", "shaft"] },
    { key: "hardware", terms: ["grub screw", "hex head screw", "screw", "bolt", "nut"] },
    { key: "engine", terms: ["engine block", "diesel engine", "engine"] },
    { key: "compressor", terms: ["air compressor", "compressor"] },
    { key: "cleaner", terms: ["air cleaner", "cleaner element"] },
    { key: "battery", terms: ["batteries", "battery terminal", "battery"] },
    { key: "radiator", terms: ["cooling radiator", "radiator"] },
    { key: "tank", terms: ["fuel tank", "oil tank", "water tank", "tank"] },
    { key: "winch", terms: ["main winch", "sand reel", "winch"] },
    { key: "tophead", terms: ["rotary tophead", "tophead"] },
    { key: "coolant", terms: ["cooling system", "coolant"] },
    { key: "hydraulic", terms: ["hydraulic reservoir", "hydraulic system", "hydraulic pump", "hydraulic filter", "hydraulic"] }
  ];
  for (const group of partClasses) {
    for (const term of group.terms) {
      if (lowerS.includes(term)) {
        return term.split(" ").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");
      }
    }
  }
  return "NA";
}

function runRuleExtractorHeuristics(text, docName, pageNum = 1) {
  const output = { maintenance: [], spare_parts: [] };

  const lowerText = text.toLowerCase();
  if (lowerText.includes("symptom") && lowerText.includes("cause") && lowerText.includes("elimination")) {
    const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    let inTable = false;
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i].replace(/\s+/g, " ");
        if (line.toLowerCase().includes("symptom") && line.toLowerCase().includes("cause")) {
            inTable = true;
            continue;
        }
        if (inTable && line.length > 10) {
            let comp = isolateComponent(line);
            if (comp !== "NA" && line.toLowerCase().includes("replace")) {
                output.maintenance.push({
                    id: 0,
                    equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
                    subsystem_component: comp,
                    maintenance_routine: "Periodic",
                    checks_instructions: line,
                    page: pageNum
                });
            }
        }
    }
  }

  const sentences = text.split(/(?<=[.?!])\s+/);
  const keywords = ["replace", "lubricate", "grease", "inspect", "check", "clean", "torque", "coaxiality", "tighten", "weld", "drain", "replenish", "flush", "tighten"];
  const partKeywords = ["bearing", "filter", "friction plate", "pad", "disc", "valve", "coupling", "seal", "clamp", "stopper", "nut", "bolt", "accumulator", "gasket", "spring", "hose", "pipe", "pump", "block", "roller", "screw", "pin", "wire", "rope", "plug", "motor", "gear", "reducer", "coupler", "fitting", "caliper", "drum", "shaft", "skid", "plates", "groove", "gearbox", "sump", "oil", "grease", "lubricant", "engine", "compressor", "air cleaner", "battery", "radiator", "tank", "cable", "winch", "tophead", "coolant", "fuel", "hydraulic"];
  
  let lastSeenComponent = "System Component"; // Contextual tracking

  sentences.forEach((sentence) => {
    let cleanSentence = sentence.trim().replace(/^(\d+[\.\)\-\s]*)+/i, "").trim();
    if (cleanSentence.startsWith("S") && cleanSentence.length < 5) return;
    const lowerS = cleanSentence.toLowerCase();
    const isHeaderOrIndicator = /^\b(table|figure|fig|section|drawing|dwg|no)\b|^\d+(\.\d+)*\b/i.test(cleanSentence);
    const isGenericHeader = /check items|maintenance regulations|troubleshooting methods|common troubles|trouble phenomena|check before|inspection before|periodic maintenance/i.test(lowerS);
    const isTOCLine = /\.{3,}/.test(cleanSentence) || /\.\s*\.\s*\.\s*\./.test(cleanSentence);
    if (isHeaderOrIndicator || isGenericHeader || isTOCLine) return;

    let componentMatch = isolateComponent(cleanSentence);
    if (componentMatch !== "NA") {
        lastSeenComponent = componentMatch;
    }

    const hasKeyword = keywords.some(kw => lowerS.includes(kw));
    if (hasKeyword && cleanSentence.length > 20 && cleanSentence.length < 250) {
      let component = componentMatch !== "NA" ? componentMatch : lastSeenComponent;
      
      let routine = "Monthly";
      if (lowerS.includes("hour")) {
        const hoursMatch = lowerS.match(/(\d{2,5})\s*hours/);
        routine = hoursMatch ? `Every ${hoursMatch[1]} Hours` : "Periodic Hours";
      } else if (lowerS.includes("month")) {
        const monthsMatch = lowerS.match(/(\d+)\s*months?/);
        routine = monthsMatch ? `Every ${monthsMatch[1]} Months` : "Monthly";
      } else if (lowerS.includes("week")) {
        routine = "Weekly";
      } else if (lowerS.includes("daily") || lowerS.includes("shift")) {
        routine = "Daily / Shift";
      } else if (lowerS.includes("yearly") || lowerS.includes("annual")) {
        routine = "Yearly";
      }
      
      output.maintenance.push({
        id: 0,
        equipment_title: docName ? docName.replace(/\.[^/.]+$/, "") : "NA",
        subsystem_component: component,
        maintenance_routine: routine,
        checks_instructions: cleanSentence,
        page: pageNum
      });
    }
  });

  output.maintenance = output.maintenance.filter(isCleanMaintenanceRow).slice(0, 10);
  return output;
}

async function run() {
  const data = new Uint8Array(fs.readFileSync('/Users/akshayryali/Downloads/LUBE OIL PUMP  SEIM-MANUAL (1).pdf'));
  const doc = await pdfjsLib.getDocument(data).promise;
  const allMaintenance = [];
  for(let i=1; i<=doc.numPages; i++) {
     const page = await doc.getPage(i);
     const content = await page.getTextContent();
     const text = content.items.map(i => i.str).join(' ');
     const res = runRuleExtractorHeuristics(text, "LUBE OIL PUMP  SEIM-MANUAL (1).pdf", i);
     allMaintenance.push(...res.maintenance);
  }
  console.log("Total Maintenance extracted:", allMaintenance.length);
  console.log("Samples from Page 33:");
  console.dir(allMaintenance.filter(x => x.page === 33), {depth: null});
}
run().catch(console.error);
