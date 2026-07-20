const fs = require('fs');

const codeToRestore = `
// Heuristic pre-filter to detect if a page contains recommended spare parts lists or tables
function isRecommendedSparePartsPage(pageText) {
  if (!pageText) return false;
  
  // Exclude Table of Contents pages by checking for dot leaders or "table of contents"
  if (/\\.{4,}/.test(pageText) || /\\.\\s*\\.\\s*\\.\\s*\\.\\s*\\./.test(pageText) || pageText.toLowerCase().includes("table of contents")) {
    return false;
  }
  
  const text = pageText.toLowerCase();
  const cleanText = text.replace(/\\s+/g, " ");
  
  // Specific headers/keywords indicating recommended or quick-wear spare parts lists
  return cleanText.includes("recommended (one year) spare parts") || 
         cleanText.includes("recommended spare parts") || 
         cleanText.includes("quick-wear parts") || 
         cleanText.includes("quick - wear parts") || 
         cleanText.includes("consumptive parts") || 
         cleanText.includes("quick-wear and consumptive") ||
         cleanText.includes("quick - wear and consumptive") ||
         cleanText.includes("bearings list of dw") ||
         (cleanText.includes("legend") && cleanText.includes("pos") && cleanText.includes("q.ty"));
}

// Specialized structural spare parts parser for Recommended and Quick-Wear spare parts tables
function parseSparePartsStructurally(text, docName, pageNum = 1) {
  const results = [];
  if (!text) return results;
  const cleanText = text.replace(/\\s+/g, " ");
  
  // Find all 10-digit codes
  const codeRegex = /\\b\\d{10}\\b/g;
  let match;
  const codeMatches = [];
  while ((match = codeRegex.exec(cleanText)) !== null) {
    codeMatches.push({
      code: match[0],
      start: match.index,
      end: codeRegex.lastIndex
    });
  }

  if (codeMatches.length === 0) {
    const lowerText = cleanText.toLowerCase();
    const legendIdx = lowerText.indexOf("legend");
    let searchArea = cleanText;
    if (legendIdx !== -1) {
      searchArea = cleanText.substring(legendIdx + "legend".length);
    }
    
    // Regex matching Pos Q.ty Description
    const regexPattern = /\\b(\\d+)\\s+(\\d+(?:-\\d+)?)\\s+([a-zA-Z\\s\\/\\\\\\-\\&\\(\\)\\.\\,\\’\\'\\"\\+]+?)(?=\\s+\\d+\\s+\\d+(?:-\\d+)?\\s+|$)/g;
    let matchPair;
    
    let subsystemLocation = "NA";
    if (lowerText.includes("with direct joint")) {
      subsystemLocation = "Direct Joint";
    } else if (lowerText.includes("with extension and one bearing")) {
      subsystemLocation = "Extension & One Bearing";
    } else if (lowerText.includes("with extension and two bearings")) {
      subsystemLocation = "Extension & Two Bearings";
    }
    
    while ((matchPair = regexPattern.exec(searchArea)) !== null) {
      const pos = matchPair[1].trim();
      const qty = matchPair[2].trim();
      const desc = matchPair[3].trim().replace(/\\s+/g, " "); // collapse spacing
      
      let categorization = "Critical Spare";
      const lowerDesc = desc.toLowerCase();
      if (lowerDesc.includes("o-ring") || lowerDesc.includes("gasket") || lowerDesc.includes("seal") || lowerDesc.includes("screw") || lowerDesc.includes("washer") || lowerDesc.includes("circlip") || lowerDesc.includes("ring nut") || lowerDesc.includes("bearing")) {
        categorization = "Consumable";
      }
      
      results.push({
        id: 0,
        equipment_title: docName ? docName.replace(/\\.[^/.]+$/, "") : "NA",
        subsystem_location: subsystemLocation,
        item_no: pos,
        part_name: desc,
        part_number_code: "NA",
        drawing_model_no: "NA",
        oem_standard_body: "NA",
        part_categorization: categorization,
        quantity: qty,
        recommended_stock_qty: "NA",
        warranty_period: "NA",
        frequency_of_use: "NA",
        page: pageNum
      });
    }
    
    return results;
  }
  
  // Table state tracking
  let currentTable = "Table 15";
  let idxCounter = 1;
  
  // Reconstruct table and index state sequentially based on sparePartsRegistry
  let prevTable = "Table 15";
  let prevIdx = 0;
  if (typeof sparePartsRegistry !== "undefined" && Array.isArray(sparePartsRegistry)) {
    const cleanDocName = docName ? docName.replace(/\\.[^/.]+$/, "") : "NA";
    for (let idx = sparePartsRegistry.length - 1; idx >= 0; idx--) {
      const r = sparePartsRegistry[idx];
      if (r.equipment_title === cleanDocName) {
        if (r.frequency_of_use && r.frequency_of_use.includes("Replace every")) {
          prevTable = "Table 16";
        } else {
          prevTable = "Table 15";
        }
        prevIdx = parseInt(r.item_no) || 0;
        break;
      }
    }
  }
  
  currentTable = prevTable;
  idxCounter = prevIdx > 0 ? prevIdx + 1 : 1;
  
  for (let i = 0; i < codeMatches.length; i++) {
    const m = codeMatches[i];
    const code = m.code;
    
    const prevEnd = i > 0 ? codeMatches[i-1].end : 0;
    const preceding = cleanText.substring(prevEnd, m.start).trim();
    
    const nextStart = (i + 1 < codeMatches.length) ? codeMatches[i+1].start : cleanText.length;
    const segment = cleanText.substring(m.end, nextStart).trim();
    
    // Determine table type and index from preceding
    const lowerPre = preceding.toLowerCase();
    if (lowerPre.includes("quick - wear") || lowerPre.includes("quick-wear") || lowerPre.includes("quick_wear")) {
      currentTable = "Table 16";
      idxCounter = 1;
    } else if (lowerPre.includes("recommended")) {
      currentTable = "Table 15";
      idxCounter = 1;
    } else if (lowerPre.includes("bearings list")) {
      currentTable = "Table 14";
      idxCounter = 1;
    }
    
    // Determine row index
    let targetIndex = idxCounter;
    const trailingDigitsMatch = preceding.match(/(\\d+(?:\\s+\\d+)*)\\s*$/);
    if (trailingDigitsMatch) {
      const digits = trailingDigitsMatch[1].replace(/\\s+/g, "");
      if (digits.endsWith(String(targetIndex))) {
        // match
      } else if (digits.endsWith(String(targetIndex + 1))) {
        targetIndex = targetIndex + 1;
      } else {
        // fallback: parse last 1-2 digits
        const val2 = parseInt(digits.slice(-2));
        if (!isNaN(val2)) {
          targetIndex = val2;
        } else {
          const val1 = parseInt(digits.slice(-1));
          if (!isNaN(val1)) {
            targetIndex = val1;
          }
        }
      }
    }
    
    const rowId = targetIndex;
    idxCounter = rowId + 1;
    
    // We discard Table 14 (Bearings list)
    if (currentTable === "Table 14") {
      continue;
    }
    
    // Parse segment
    let nextIdxStr = String(idxCounter);
    let nextIdxSpaceStr = nextIdxStr.split("").join(" ");
    
    let segmentClean = segment;
    // Strip next index
    const patterns = [
      new RegExp("\\\\s+" + escapeRegExp(nextIdxSpaceStr) + "$"),
      new RegExp("\\\\s+" + escapeRegExp(nextIdxStr) + "$")
    ];
    for (const pat of patterns) {
      const matchPat = segmentClean.match(pat);
      if (matchPat) {
        segmentClean = segmentClean.substring(0, matchPat.index).trim();
        break;
      }
    }
    
    // Strip Table 16 header if Table 15 last row
    if (currentTable === "Table 15" && segmentClean.toLowerCase().includes("list of quick")) {
      const matchHeader = segmentClean.match(/\\b\\d+(?:\\s+\\d+)?\\s+list of quick.*$/i);
      if (matchHeader) {
        segmentClean = segmentClean.substring(0, matchHeader.index).trim();
      }
    }
    
    // Strip Table 17 header or other sections
    if (segmentClean.toLowerCase().includes("quality assurance")) {
      const matchHeader = segmentClean.match(/\\b\\d+(?:\\s+\\d+)?\\s+quality assurance.*$/i);
      if (matchHeader) {
        segmentClean = segmentClean.substring(0, matchHeader.index).trim();
      }
    }
    
    const tokens = segmentClean.split(/\\s+/);
    
    let qty = "NA";
    let warranty = "NA";
    let remark = "NA";
    
    if (currentTable === "Table 16") {
      if (tokens.length >= 2 && ["year", "years", "month", "months", "monthes"].includes(tokens[tokens.length - 1].toLowerCase())) {
        warranty = tokens[tokens.length - 2] + " " + tokens[tokens.length - 1];
        tokens.splice(tokens.length - 2, 2);
      } else if (tokens.length >= 1 && tokens[tokens.length - 1].toLowerCase().includes("year")) {
        warranty = tokens[tokens.length - 1];
        tokens.splice(tokens.length - 1, 1);
      }
    }
    
    // Extract QTY (check last 3 tokens, group consecutive digits)
    const maxChecked = Math.max(0, tokens.length - 3);
    for (let j = tokens.length - 1; j >= maxChecked; j--) {
      if (/^\\d+$/.test(tokens[j])) {
        let startJ = j;
        while (startJ > 0 && /^\\d+$/.test(tokens[startJ - 1])) {
          startJ--;
        }
        qty = tokens.slice(startJ, j + 1).join(" ");
        remark = tokens.slice(j + 1).join(" ");
        tokens.splice(startJ, tokens.length - startJ);
        break;
      }
    }
    
    // Token classification
    const isCode = (s) => {
      const hasDigitOrSpecial = /[0-9\\-\\/\\.\\;×φ]/.test(s);
      const isUpperWord = (s === s.toUpperCase() && s.length >= 2);
      return hasDigitOrSpecial || isUpperWord;
    };
    
    const drawingModel = [];
    const partNameTokens = [];
    const specModel = [];
    
    let state = "standard";
    for (const t of tokens) {
      if (state === "standard") {
        if (isCode(t)) {
          drawingModel.push(t);
        } else {
          state = "name";
          partNameTokens.push(t);
        }
      } else if (state === "name") {
        if (isCode(t)) {
          state = "model";
          specModel.push(t);
        } else {
          partNameTokens.push(t);
        }
      } else if (state === "model") {
        specModel.push(t);
      }
    }
    
    let partName = partNameTokens.join(" ").trim();
    let drawingModelNo = drawingModel.join(" ").trim();
    let mfrCode = specModel.join(" ").trim();
    
    if (!partName && drawingModelNo) {
      drawingModelNo = drawingModel[0];
      mfrCode = drawingModel.slice(1).join(" ");
      partName = "NA";
    }
    
    if (!partName) partName = "NA";
    if (!drawingModelNo) drawingModelNo = "NA";
    if (!mfrCode) mfrCode = "NA";
    
    let categorization = (currentTable === "Table 16") ? "Consumable" : "Critical Spare";
    const lowerName = partName.toLowerCase();
    if (lowerName.includes("filter") || lowerName.includes("seal") || lowerName.includes("stopper") || lowerName.includes("holder") || lowerName.includes("rope") || lowerName.includes("oil")) {
      categorization = "Consumable";
    }
    
    let frequency = "NA";
    if (currentTable === "Table 16") {
      if (warranty !== "NA") {
        frequency = "Replace every " + warranty;
      }
    } else if (currentTable === "Table 15") {
      if (rowId === 6) {
        frequency = "Replace every 6 months";
      } else {
        frequency = "Replace during overhaul / Medium";
      }
    }
    
    results.push({
      id: 0,
      equipment_title: docName ? docName.replace(/\\.[^/.]+$/, "") : "NA",
      subsystem_location: "NA",
      item_no: String(rowId),
      part_name: partName,
      part_number_code: code,
      drawing_model_no: (drawingModelNo !== "NA" && mfrCode !== "NA") ? (drawingModelNo + " / " + mfrCode) : (drawingModelNo !== "NA" ? drawingModelNo : (mfrCode !== "NA" ? mfrCode : "NA")),
      oem_standard_body: "NA",
      part_categorization: categorization,
      quantity: qty !== "NA" ? qty : "1",
      recommended_stock_qty: "NA",
      warranty_period: warranty,
      frequency_of_use: frequency,
      page: pageNum
    });
  }
  
  return results;
}

function escapeRegExp(string) {
  return string.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
}

`;

let appCode = fs.readFileSync('/Users/akshayryali/1/app.js', 'utf-8');

// Insert it before shouldProcessPageWithLLM
appCode = appCode.replace(
  /function shouldProcessPageWithLLM\(pageText\)/,
  codeToRestore + '\nfunction shouldProcessPageWithLLM(pageText)'
);

// Inject interception branch into runOllamaExtractor
const extractInterception = `
  if (isRecommendedSparePartsPage(text)) {
    const spareParts = parseSparePartsStructurally(text, docName, pageNum);
    return normalizeExtraction({
      maintenance: [],
      spare_parts: spareParts
    });
  }
`;

appCode = appCode.replace(
  /async function runOllamaExtractor\(text, docName, pageNum\) \{/,
  'async function runOllamaExtractor(text, docName, pageNum) {' + extractInterception
);

fs.writeFileSync('/Users/akshayryali/1/app.js', appCode);
console.log('Restored structural parser successfully.');
