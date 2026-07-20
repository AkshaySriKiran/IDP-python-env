const fs = require('fs');

async function run() {
  const base64Image = fs.readFileSync('/Users/akshayryali/1/test_pdf.png', { encoding: 'base64' });

  const systemPrompt = `Please transcribe all the handwritten and printed text you see in the provided image. Do not format it as JSON, just write out the text exactly as it appears.`;

  const fetchBody = {
    model: "llama3.2-vision:latest",
    prompt: systemPrompt,
    stream: false,
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
