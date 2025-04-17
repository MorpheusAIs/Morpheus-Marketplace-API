const logger = require('../utils/logger');
const sessionService = require('../services/session-service');
const userService = require('../services/user-service');
const modelMappingService = require('../services/model-mapping-service');
const keyVaultService = require('../services/key-vault-service');
const { createError } = require('../utils/error-utils');

/**
 * Controller for OpenAI-compatible chat completions endpoint
 */
class ChatCompletionsController {
  /**
   * Create a chat completion
   * @param {Object} req - Express request object
   * @param {Object} res - Express response object
   * @returns {Promise<void>}
   */
  async createChatCompletion(req, res) {
    try {
      const { model, messages, stream = false, temperature, max_tokens, ...otherParams } = req.body;
      
      // Validate request
      if (!model) {
        return res.status(400).json(createError('model is required'));
      }
      
      if (!messages || !Array.isArray(messages) || messages.length === 0) {
        return res.status(400).json(createError('messages array is required and cannot be empty'));
      }
      
      // Get user from API key
      const apiKey = req.headers.authorization?.split(' ')[1];
      const user = await userService.getUserByApiKey(apiKey);
      
      if (!user) {
        return res.status(401).json(createError('Invalid API key'));
      }
      
      // Check if we have a private key for this API key
      const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
      if (!hasPrivateKey) {
        return res.status(400).json(createError(
          'No private key associated with this API key. Please register your private key first.',
          'missing_private_key'
        ));
      }
      
      // Map OpenAI model name to blockchain model ID
      const modelId = await modelMappingService.getModelIdFromName(model);
      
      if (!modelId) {
        return res.status(400).json(createError(`Model ${model} is not available`));
      }

      // Either use an existing active session or create a new one
      let session;
      
      // Check if there's an active session for this API key and model
      session = await sessionService.getSessionForApiKeyAndModel(apiKey, modelId);
      
      if (!session) {
        // Create a new session
        session = await sessionService.createSession(
          apiKey,
          user.id,
          modelId,
          600, // 10 minute session
          true,  // direct payment
          true   // failover enabled
        );
      }
      
      // Prepare prompt payload
      const promptPayload = {
        model,
        messages,
        temperature: temperature || parseFloat(process.env.TEMPERATURE_DEFAULT) || 0.7,
        max_tokens: max_tokens || parseInt(process.env.MAX_TOKENS_DEFAULT) || 1024,
        stream,
        ...otherParams
      };
      
      if (stream) {
        // Set headers for streaming response
        res.setHeader('Content-Type', 'text/event-stream');
        res.setHeader('Cache-Control', 'no-cache');
        res.setHeader('Connection', 'keep-alive');
        
        // Stream handler
        const sendChunk = (chunk) => {
          res.write(`data: ${JSON.stringify(chunk)}\n\n`);
          
          // If this is the last chunk, end with [DONE]
          if (chunk.choices?.[0]?.finish_reason) {
            res.write('data: [DONE]\n\n');
          }
        };
        
        try {
          await sessionService.sendPrompt(apiKey, session.id, promptPayload, sendChunk);
          res.end();
        } catch (error) {
          // If an error occurs midstream, send error and close
          res.write(`data: ${JSON.stringify({ error: { message: error.message } })}\n\n`);
          res.end();
        }
      } else {
        // Non-streaming response
        try {
          const response = await sessionService.sendPrompt(apiKey, session.id, promptPayload);
          res.json(response);
        } catch (error) {
          res.status(500).json(createError(error.message));
        }
      }
    } catch (error) {
      logger.error('Error in chat completions:', error);
      res.status(500).json(createError('Internal server error'));
    }
  }
  
  /**
   * List available models (OpenAI compatible)
   * @param {Object} req - Express request object
   * @param {Object} res - Express response object
   * @returns {Promise<void>}
   */
  async listModels(req, res) {
    try {
      // Get API key from authorization header
      const apiKey = req.headers.authorization?.split(' ')[1];
      
      // Check if we have a private key for this API key
      const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
      if (!hasPrivateKey) {
        return res.status(400).json(createError(
          'No private key associated with this API key. Please register your private key first.',
          'missing_private_key'
        ));
      }
      
      const models = await modelMappingService.listAvailableModels(apiKey);
      
      const formattedModels = {
        object: 'list',
        data: models.map(model => ({
          id: model.openai_name,
          object: 'model',
          created: Math.floor(Date.now() / 1000),
          owned_by: 'morpheus',
          permission: [{
            id: 'modelperm-' + model.id,
            object: 'model_permission',
            created: Math.floor(Date.now() / 1000),
            allow_create_engine: false,
            allow_sampling: true,
            allow_logprobs: true,
            allow_search_indices: false,
            allow_view: true,
            allow_fine_tuning: false,
            organization: '*',
            group: null,
            is_blocking: false
          }],
          root: model.openai_name,
          parent: null
        }))
      };
      
      res.json(formattedModels);
    } catch (error) {
      logger.error('Error fetching models:', error);
      res.status(500).json(createError('Failed to fetch models'));
    }
  }
}

module.exports = new ChatCompletionsController(); 