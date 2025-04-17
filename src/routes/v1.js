const express = require('express');
const router = express.Router();
const chatCompletionsController = require('../controllers/chat-completions-controller');
const { authenticate } = require('../middleware/auth');
const sessionService = require('../services/session-service');
const keyVaultService = require('../services/key-vault-service');
const modelMappingService = require('../services/model-mapping-service');
const { createError } = require('../utils/error-utils');
const logger = require('../utils/logger');

// Models endpoints
router.get('/models', authenticate, chatCompletionsController.listModels);

// Chat completions endpoint
router.post('/chat/completions', authenticate, chatCompletionsController.createChatCompletion);

// Session management endpoints
// These are specific to Morpheus and not in the OpenAI API
router.get('/sessions', authenticate, async (req, res) => {
  try {
    const apiKey = req.headers.authorization.split(' ')[1];
    
    // Check if private key exists
    const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
    
    if (!hasPrivateKey) {
      return res.status(400).json(createError(
        'No private key associated with this API key. Please register your private key first.',
        'missing_private_key'
      ));
    }
    
    // List all active sessions for the user
    const sessions = await sessionService.getUserSessions(req.user.id);
    
    res.status(200).json(sessions);
  } catch (error) {
    logger.error('Error listing sessions:', error);
    res.status(500).json(createError('Failed to list sessions'));
  }
});

router.post('/sessions', authenticate, async (req, res) => {
  try {
    const { modelId, durationSeconds = 600, directPayment = true, enableFailover = true } = req.body;
    const apiKey = req.headers.authorization.split(' ')[1];
    
    if (!modelId) {
      return res.status(400).json(createError('modelId is required'));
    }
    
    // Check if private key exists
    const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
    
    if (!hasPrivateKey) {
      return res.status(400).json(createError(
        'No private key associated with this API key. Please register your private key first.',
        'missing_private_key'
      ));
    }
    
    // Create a new session
    const session = await sessionService.createSession(
      apiKey,
      req.user.id,
      modelId,
      durationSeconds,
      directPayment,
      enableFailover
    );
    
    res.status(200).json(session);
  } catch (error) {
    logger.error('Error creating session:', error);
    res.status(500).json(createError(`Failed to create session: ${error.message}`));
  }
});

router.delete('/sessions/:sessionId', authenticate, async (req, res) => {
  try {
    const { sessionId } = req.params;
    const apiKey = req.headers.authorization.split(' ')[1];
    
    if (!sessionId) {
      return res.status(400).json(createError('sessionId is required'));
    }
    
    // Check if private key exists
    const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
    
    if (!hasPrivateKey) {
      return res.status(400).json(createError(
        'No private key associated with this API key. Please register your private key first.',
        'missing_private_key'
      ));
    }
    
    // Check if session exists and belongs to user
    const session = await sessionService.getSession(sessionId);
    
    if (!session) {
      return res.status(404).json(createError('Session not found'));
    }
    
    if (session.userId !== req.user.id) {
      return res.status(403).json(createError('You do not have permission to close this session'));
    }
    
    // Close the session
    await sessionService.closeSession(apiKey, sessionId);
    
    res.status(200).json({ success: true });
  } catch (error) {
    logger.error(`Error closing session ${req.params.sessionId}:`, error);
    res.status(500).json(createError(`Failed to close session: ${error.message}`));
  }
});

// Update .env params endpoint - for development only
// This allows updating parameters like which contract address to use
if (process.env.NODE_ENV === 'development') {
  router.post('/dev/update-env', authenticate, (req, res) => {
    try {
      const { key, value } = req.body;
      
      if (!key || !value) {
        return res.status(400).json(createError('key and value are required'));
      }
      
      // Only allow updating certain keys
      const allowedKeys = ['CONTRACT_ADDRESS', 'PROXY_ROUTER_URL'];
      
      if (!allowedKeys.includes(key)) {
        return res.status(400).json(createError(`Cannot update ${key}. Allowed keys: ${allowedKeys.join(', ')}`));
      }
      
      // Update environment variable
      process.env[key] = value;
      
      res.status(200).json({ success: true, key, value });
    } catch (error) {
      logger.error('Error updating environment variable:', error);
      res.status(500).json(createError('Failed to update environment variable'));
    }
  });
}

// Future endpoints to implement:
// - /v1/completions (legacy completions)
// - /v1/embeddings (vector embeddings)
// - /v1/fine-tuning (for fine-tuning models)

module.exports = router; 