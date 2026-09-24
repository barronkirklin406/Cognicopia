import express from 'express';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;
const HOST = '0.0.0.0';

app.use(express.json({ limit: '10mb' }));

// Health check endpoint
app.get('/api/health', (req, res) => {
  res.json({ status: 'ok', app: 'Cognicopia' });
});

// Optional payload saving endpoint
app.post('/api/payloads', (req, res) => {
  try {
    const payloadDir = path.join(__dirname, 'payloads');
    if (!fs.existsSync(payloadDir)) {
      fs.mkdirSync(payloadDir, { recursive: true });
    }
    const timestamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
    const filename = `cognicopia_payload_${timestamp}.json`;
    const filePath = path.join(payloadDir, filename);
    fs.writeFileSync(filePath, JSON.stringify(req.body, null, 2), 'utf-8');
    res.json({ success: true, filename });
  } catch (err) {
    res.status(500).json({ error: 'Failed to save payload', details: err.message });
  }
});

// Clean URLs for resource pages
const pageRoutes = [
  'about',
  'features',
  'faq',
  'therapy',
  'contact',
  'zentangle-art',
  'cognitive-journals',
  'life-planners',
  'clinical-alignment',
  'high-volume-facilities',
  'publishing-excellence'
];
pageRoutes.forEach(route => {
  app.get(`/${route}`, (req, res) => {
    const htmlPath = path.join(__dirname, `${route}.html`);
    if (fs.existsSync(htmlPath)) {
      res.sendFile(htmlPath);
    } else {
      res.redirect('/');
    }
  });
});

// Serve static assets and root directory
app.use(express.static(__dirname, {
  extensions: ['html', 'htm']
}));

// Fallback to index.html
app.use((req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, HOST, () => {
  console.log(`Cognicopia server running on http://${HOST}:${PORT}`);
});
