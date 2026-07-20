const fs = require('fs');

async function runTest() {
  const base64Image = fs.readFileSync('test_pdf.png', { encoding: 'base64' });

  const systemPrompt = `You are an expert transcriber of handwritten field history cards and maintenance logbooks.
Your task is to analyze the image or text below and extract historical maintenance log entries exactly as they are written.

Group your extractions into the "maintenance" list. Return an empty array [] for "spare_parts".
If a field is missing, not specified, or not available in the text, you MUST populate it with the string "NA".

You MUST strictly use the following 5 keys for every entry:
- "date"
- "maintenance_work_description"
- "parts_renewed"
- "attended_by"
- "remarks"

Response MUST be strictly valid JSON (and only JSON, with no other text before or after).
CRITICAL: Even if the page looks like a cover page, or the table is messy and handwritten, DO NOT return empty arrays! You MUST attempt to extract whatever handwritten notes, signatures, or dates are visible into the "maintenance" list.

CRITICAL INSTRUCTION: DO NOT use the values from the example output. If a field is missing or not found in the text, you MUST output "NA".

Example Output Structure:
{
  "maintenance": [
    {
      "date": "15 Jan 2023",
      "maintenance_work_description": "Repl. Oil Pump",
      "parts_renewed": "Oil Pump Assy",
      "attended_by": "J. P. H.",
      "remarks": "Tested OK"
    }
  ],
  "spare_parts": []
}`;

  const fetchBody = {
    model: 'llama3.2-vision:latest',
    prompt: systemPrompt,
    stream: false,
    format: "json",
    images: [base64Image],
    options: {
      temperature: 0.0
    }
  };

  console.log("Sending request to Ollama...");
  const response = await fetch(`http://localhost:11434/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(fetchBody)
  });

  const data = await response.json();
  console.log("Raw Response:");
  console.log(data.response);
}
runTest();
