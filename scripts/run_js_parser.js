const fs = require('fs');

function isCleanMaintenanceRow(row) {
  const comp = row.subsystem_component || "NA";
  if (comp === "NA") return false;
  const checks = row.checks_instructions || "NA";
  if (checks === "NA") return false;
  return true;
}

function isolateComponent(sentence) {
  const lowerS = sentence.toLowerCase();
  
  // High-fidelity physical parts dictionary
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
    { key: "hardware", terms: ["grub screw", "hex head screw", "screw", "bolt", "nut"] }
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
  const output = {
    maintenance: [],
    spare_parts: []
  };
  
  const sentences = text.split(/(?<=[.?!])\s+/);
  
  const keywords = ["replace", "lubricate", "grease", "inspect", "check", "clean", "torque", "coaxiality", "tighten", "weld", "drain", "replenish"];
  const partKeywords = ["bearing", "filter", "friction plate", "pad", "disc", "valve", "coupling", "seal", "clamp", "stopper", "nut", "bolt", "accumulator", "gasket", "spring", "hose", "pipe", "pump", "block", "roller", "screw", "pin", "wire", "rope", "plug", "motor", "gear", "reducer", "coupler", "fitting", "caliper", "drum", "shaft", "skid", "plates", "groove", "gearbox", "sump", "oil", "grease", "lubricant"];
  
  sentences.forEach((sentence) => {
    let cleanSentence = sentence.trim().replace(/^(\d+[\.\)\-\s]*)+/i, "").trim();
    if (cleanSentence.startsWith("S") && cleanSentence.length < 5) return;
    
    const lowerS = cleanSentence.toLowerCase();

    const isHeaderOrIndicator = /^\b(table|figure|fig|section|drawing|dwg|no)\b|^\d+(\.\d+)*\b/i.test(cleanSentence);
    const isGenericHeader = /check items|maintenance regulations|troubleshooting methods|common troubles|trouble phenomena|check before|inspection before|periodic maintenance/i.test(lowerS);
    const isTOCLine = /\.{3,}/.test(cleanSentence) || /\.\s*\.\s*\.\s*\./.test(cleanSentence);
    if (isHeaderOrIndicator || isGenericHeader || isTOCLine) return;

    const hasKeyword = keywords.some(kw => lowerS.includes(kw));
    const hasPart = partKeywords.some(pk => lowerS.includes(pk));
    
    if (hasKeyword && cleanSentence.length > 20 && cleanSentence.length < 250) {
      let component = isolateComponent(cleanSentence);
      
      let routine = "Monthly";
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

const extractedText = fs.readFileSync("extracted_text.json", "utf-8");
const pages = JSON.parse(extractedText);

let allMaintenance = [];

for (const [pageNum, text] of Object.entries(pages)) {
    const result = runRuleExtractorHeuristics(text, "LUBE OIL PUMP", parseInt(pageNum));
    allMaintenance.push(...result.maintenance);
}

console.log("Total Maintenance Items:", allMaintenance.length);
