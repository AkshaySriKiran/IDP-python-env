const fs = require('fs');

async function run() {
  const base64Image = fs.readFileSync('/Users/akshayryali/1/test_pdf.png', { encoding: 'base64' });

  const systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.

Group your extractions into two distinct JSON lists: "maintenance" and "spare_parts".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA". Do not use null, undefined, or empty values.

Rules for "maintenance" tasks:
- Extract real maintenance tasks, checks, inspection routines, adjustments, or replacements.
- For "equipment_title", default to "Rig#14 Electrical History ccard" if the text does not mention a specific equipment.
- For "subsystem_component", you MUST identify a specific, physical sub-system or component.
- For "maintenance_routine", extract the interval. If no interval is specified, output "Periodic".
- For "checks_instructions", write the procedure or actions in a concise manner.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components.
- For "equipment_title", default to "Rig#14 Electrical History ccard" if not specified.
- For "part_categorization", use "Critical Spare", "Consumable", "Standard Part", or "NA".
- For "quantity", extract the number of units installed/used per assembly (default to "1").
- For "part_number_code": The manufacturer's part number or code.
- For "drawing_model_no": The engineering drawing or model designator number.
- For "recommended_stock_qty", extract stock recommendation levels if present (default to "NA").
- For "frequency_of_use", extract how frequently this part is used, replaced, or needs attention.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL: If the page is clearly a Table of Contents or Index with no actual parts/maintenance data, return exactly: {"maintenance": [], "spare_parts": []}. CRITICAL EXCEPTION: Do NOT return empty arrays if you see actual part names accompanied by alphanumeric codes. If specific parts exist, you MUST extract them regardless of the surrounding layout.

Text to parse:
"""
OCR VISION EXTRACTION - Use provided image to extract text.
"""`;

  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: systemPrompt,
    stream: false,
    format: "json",
    images: [base64Image],
    options: {
      temperature: 0.0
    }
  };

  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(fetchBody)
  });

  const data = await response.json();
  console.log(data.response);
}

run();
