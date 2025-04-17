require('dotenv').config();
const express = require('express');
const cors = require('cors');
const helmet = require('helmet');
const rateLimit = require('express-rate-limit');
const swaggerUi = require('swagger-ui-express');
const path = require('path');
const fs = require('fs');
const logger = require('./utils/logger');
const routes = require('./routes');
const { errorHandler } = require('./middleware/error-handler');

// Create Express app
const app = express();
const port = process.env.PORT || 3000;

// Apply security middleware
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      scriptSrc: ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net"],
      styleSrc: ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net"],
      imgSrc: ["'self'", "data:"],
      connectSrc: ["'self'"],
      fontSrc: ["'self'", "https://cdn.jsdelivr.net"],
      objectSrc: ["'none'"],
      frameAncestors: ["'self'"],
      formAction: ["'self'"]
    }
  }
}));
app.use(cors({ origin: process.env.CORS_ORIGIN || '*' }));
app.use(express.json());

// Apply rate limiting
const limiter = rateLimit({
  windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS) || 60000, // 1 minute
  max: parseInt(process.env.RATE_LIMIT_MAX_REQUESTS) || 100, // limit each IP to 100 requests per windowMs
  standardHeaders: true,
  legacyHeaders: false,
});
app.use('/v1', limiter);
app.use('/auth', limiter);

// Setup logging middleware
app.use((req, res, next) => {
  logger.info(`${req.method} ${req.url}`, {
    ip: req.ip,
    userAgent: req.headers['user-agent'],
  });
  next();
});

// Serve static files from public directory
app.use(express.static(path.join(__dirname, '../public')));

// Serve Swagger UI
try {
  const swaggerDocument = JSON.parse(fs.readFileSync(path.join(__dirname, '../swagger.json'), 'utf8'));
  app.use('/api-docs', swaggerUi.serve, swaggerUi.setup(swaggerDocument, {
    customCss: `
      .swagger-ui .topbar { display: none }
      .swagger-ui { 
        background-color: #1a1a1a;
        color: #e9ecef;
      }
      .swagger-ui .info .title,
      .swagger-ui .info h1,
      .swagger-ui .info h2,
      .swagger-ui .info h3,
      .swagger-ui .info h4,
      .swagger-ui .info h5,
      .swagger-ui a.nostyle,
      .swagger-ui .parameter__name,
      .swagger-ui .opblock .opblock-summary-operation-id, 
      .swagger-ui .opblock .opblock-summary-path,
      .swagger-ui .opblock .opblock-summary-description,
      .swagger-ui .model-title,
      .swagger-ui label,
      .swagger-ui .tab li,
      .swagger-ui table thead tr th {
        color: #e9ecef;
      }
      .swagger-ui .opblock {
        margin: 0 0 15px;
        border-radius: 8px;
        background: rgba(0,0,0,0.2);
        box-shadow: 0 0 10px rgba(0,0,0,0.3);
      }
      .swagger-ui .opblock-tag {
        color: #e9ecef;
        font-weight: bold;
      }
      .swagger-ui .opblock .opblock-summary {
        padding: 12px;
      }
      .swagger-ui .opblock-tag:hover {
        background-color: rgba(255,255,255,0.05);
      }
      .swagger-ui input[type=text], 
      .swagger-ui textarea {
        background-color: #2d2d2d;
        color: #e9ecef;
        border: 1px solid #444;
      }
      .swagger-ui select {
        background-color: #2d2d2d;
        color: #e9ecef;
        border: 1px solid #444;
      }
      .swagger-ui button.btn {
        transition: all 0.3s ease;
      }
      .swagger-ui button.execute {
        background-color: #0d6efd;
        color: white;
        border-radius: 4px;
      }
      .swagger-ui button.execute:hover {
        background-color: #0b5ed7;
      }
      .swagger-ui .btn-group {
        padding: 0.5rem 0;
      }
      .swagger-ui .response-col_status {
        color: #e9ecef;
      }
      .swagger-ui .response-col_description__inner p, .swagger-ui .response-col_description__inner span {
        color: #e9ecef;
      }
      .swagger-ui .responses-inner h4, .swagger-ui .responses-inner h5 {
        color: #e9ecef;
      }
      .swagger-ui .opblock-body pre {
        background-color: #2d2d2d;
        color: #e9ecef;
      }
      .swagger-ui .scheme-container {
        background-color: #222222;
        box-shadow: 0 1px 2px 0 rgba(0,0,0,0.15);
      }
      .swagger-ui section.models {
        background-color: #222222;
      }
      .swagger-ui section.models.is-open h4 {
        color: #e9ecef;
      }
      .swagger-ui .model-box {
        background-color: rgba(0,0,0,0.1);
      }
      .swagger-ui .dialog-ux .modal-ux {
        background: #222222;
        border: 1px solid #444;
      }
      .swagger-ui .dialog-ux .modal-ux-header h3 {
        color: #e9ecef;
      }
      .swagger-ui .dialog-ux .modal-ux-content {
        color: #e9ecef;
      }
    `,
    swaggerOptions: {
      docExpansion: 'list',
      filter: true,
      showExtensions: true,
      showCommonExtensions: true,
      displayRequestDuration: true,
      persistAuthorization: true,
      tryItOutEnabled: true,
      defaultModelsExpandDepth: 3,
      defaultModelExpandDepth: 3
    }
  }));
  logger.info('Swagger UI initialized successfully');
} catch (error) {
  logger.error('Failed to initialize Swagger UI:', error);
}

// Health check endpoint
app.get('/health', (req, res) => {
  res.status(200).json({ status: 'ok', timestamp: new Date().toISOString() });
});

// API Routes
app.use('/auth', routes.auth);
app.use('/v1', routes.v1);

// Root route - redirect to HTML page
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, '../public/index.html'));
});

// Apply error handler middleware
app.use(errorHandler);

// 404 Handler
app.use((req, res) => {
  res.status(404).json({
    error: {
      message: 'Not Found',
      type: 'not_found',
      code: 404
    }
  });
});

// Start the server
app.listen(port, () => {
  logger.info(`Morpheus API Gateway running on port ${port}`);
  logger.info(`Environment: ${process.env.NODE_ENV || 'development'}`);
  logger.info(`API documentation available at: http://localhost:${port}/api-docs`);
  logger.info(`Enhanced API documentation available at: http://localhost:${port}/api-documentation.html`);
  logger.info(`Frontend available at: http://localhost:${port}`);
});

// Handle uncaught exceptions
process.on('uncaughtException', (error) => {
  logger.error('Uncaught Exception:', error);
  process.exit(1);
});

// Handle unhandled promise rejections
process.on('unhandledRejection', (reason, promise) => {
  logger.error('Unhandled Rejection at:', promise, 'reason:', reason);
  process.exit(1);
});

module.exports = app; 