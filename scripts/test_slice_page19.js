const fs = require('fs');
const pdfjsLib = require('pdfjs-dist/legacy/build/pdf.js');
const { createCanvas } = require('canvas');

async function extractFromImage(base64Image) {
  const cleanDocName = "EXAMPLE_EQUIPMENT_DO_NOT_COPY";
  let systemPrompt = `You are an expert technical parser of industrial engineering manuals.
Your task is to analyze the text page content below and extract:
1. Maintenance routines, checks, and instructions.
2. Spare parts and components referenced in drawings or lists.

Group your extractions into two distinct JSON lists: "maintenance" and "spare_parts".
CRITICAL OPTIMIZATION: If a field is missing, not specified, or not available in the text, you MUST OMIT the key entirely from the JSON object. Do NOT output keys with "NA", null, or empty values. This is critical to save generation time.

Rules for "spare_parts":
- Extract items that represent real spare parts, consumables, hardware, or components.
- For "part_name", extract the descriptive name of the component or part.
- For "quantity", extract the number of units.
- For "part_number_code": The manufacturer's part number or code.
- For "drawing_model_no": The engineering drawing or model designator number.

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{
  "maintenance": [],
  "spare_parts": [
    {
      "equipment_title": "EXAMPLE_EQUIPMENT_DO_NOT_COPY",
      "subsystem_location": "Regulator",
      "item_no": "1",
      "part_name": "EXAMPLE_PART_NAME_DO_NOT_COPY",
      "part_number_code": "EXAMPLE_CODE",
      "quantity": "1"
    }
  ]
}
`;

  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: systemPrompt,
    stream: false,
    format: "json",
    images: [base64Image],
    options: { temperature: 0.1 }
  };

  const response = await fetch("http://localhost:11434/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchBody)
  });

  const respData = await response.json();
  return JSON.parse(respData.response.trim());
}

async function run() {
  const filePath = '/Users/akshayryali/Downloads/12.BOP Control System And High Pressure Test Unit Master Parts Catalog.pdf';
  const data = new Uint8Array(fs.readFileSync(filePath));
  const pdf = await pdfjsLib.getDocument({data}).promise;
  const page = await pdf.getPage(19); // 0-indexed is 18
  
  const viewport = page.getViewport({ scale: 2.0 });
  const canvas = createCanvas(viewport.width, viewport.height);
  const ctx = canvas.getContext('2d');
  await page.render({ canvasContext: ctx, viewport: viewport }).promise;
  
  // Slice 1: Top Half
  const canvas1 = createCanvas(viewport.width, viewport.height / 2);
  const ctx1 = canvas1.getContext('2d');
  ctx1.drawImage(canvas, 0, 0, viewport.width, viewport.height / 2, 0, 0, viewport.width, viewport.height / 2);
  const b64_1 = canvas1.toDataURL('image/jpeg').split(',')[1];
  
  // Slice 2: Bottom Half
  const canvas2 = createCanvas(viewport.width, viewport.height / 2);
  const ctx2 = canvas2.getContext('2d');
  ctx2.drawImage(canvas, 0, viewport.height / 2, viewport.width, viewport.height / 2, 0, 0, viewport.width, viewport.height / 2);
  const b64_2 = canvas2.toDataURL('image/jpeg').split(',')[1];
  
  console.log("Extracting Slice 1...");
  const res1 = await extractFromImage(b64_1);
  console.log("Slice 1:", res1.spare_parts);
  
  console.log("Extracting Slice 2...");
  const res2 = await extractFromImage(b64_2);
  console.log("Slice 2:", res2.spare_parts);
}

run().catch(console.error);
