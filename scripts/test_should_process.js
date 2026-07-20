const fs = require('fs');

const equipmentManifest = JSON.parse(fs.readFileSync('equipment_manifest.json', 'utf8'));
let activeEquipmentCategory = "Drawworks";

function shouldProcessPageWithLLM(pageText) {
  if (!pageText) return false;
  
  // Reject Table of Contents / Index pages to prevent LLM hallucination
  const tocRegex = /(\.{5,}|\.\s\.\s\.\s\.\s\.)/g;
  const tocMatches = pageText.match(tocRegex);
  if (tocMatches && tocMatches.length > 4) {
    return false;
  }

  const text = pageText.toLowerCase();
  const cleanText = text.replace(/\s+/g, ' ');
  
  // High-value keywords for maintenance and parts
  const keywords = (equipmentManifest && equipmentManifest.categories[activeEquipmentCategory]) 
    ? equipmentManifest.categories[activeEquipmentCategory].keywords 
    : ["replace", "lubricate", "grease", "inspect", "maintenance"];
  
  return keywords.some(kw => cleanText.includes(kw));
}

console.log("Drawworks keyword match:", shouldProcessPageWithLLM("This is a drawworks manual"));
