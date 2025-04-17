const redisClient = require('../utils/redis');
const logger = require('../utils/logger');
const proxyRouterClient = require('./proxy-router-client');

// Redis keys
const MODELS_CACHE_KEY = 'models:all';
const API_KEY_MODELS_CACHE_KEY = 'api_key:models:';
const MODEL_MAPPING_KEY_PREFIX = 'model:mapping:';
const CACHE_TTL = 3600; // 1 hour in seconds

/**
 * Model Mapping Service
 * Handles mapping between OpenAI model names and blockchain model IDs
 */
class ModelMappingService {
  constructor() {
    // Default model mapping
    this.defaultMapping = {
      'gpt-4': '0x0000000000000000000000000000000000000000000000000000000000000003', // Maps to Claude-3-5-sonnet in the example config
      'gpt-4-turbo': '0x0000000000000000000000000000000000000000000000000000000000000003',
      'gpt-3.5-turbo': '0x0000000000000000000000000000000000000000000000000000000000000005', // Maps to gpt-4o-mini in the example config
      'gpt-4o': '0x0000000000000000000000000000000000000000000000000000000000000003',
      'gpt-4o-mini': '0x0000000000000000000000000000000000000000000000000000000000000005',
      'text-embedding-ada-002': '0x0000000000000000000000000000000000000000000000000000000000000000', // Placeholder for embedding model
    };
  }
  
  /**
   * Refresh the models cache for a specific API key
   * @param {string} apiKey - User's API key
   * @returns {Promise<Array>} List of models
   */
  async refreshModelsCache(apiKey) {
    try {
      // Fetch models from blockchain via proxy router
      const models = await proxyRouterClient.getBlockchainModels(apiKey);
      
      if (models && models.length > 0) {
        // Store in Redis with API key specificity
        await redisClient.set(
          `${API_KEY_MODELS_CACHE_KEY}${apiKey}`,
          JSON.stringify(models),
          'EX',
          CACHE_TTL
        );
        
        logger.info(`Models cache refreshed for API key: ${apiKey.substring(0, 5)}...`);
        return models;
      } else {
        logger.warn(`No models returned from blockchain for API key: ${apiKey.substring(0, 5)}...`);
        return [];
      }
    } catch (error) {
      logger.error('Failed to refresh models cache:', error);
      return [];
    }
  }
  
  /**
   * Get model ID from OpenAI-compatible model name
   * @param {string} modelName - The OpenAI-compatible model name
   * @param {string} apiKey - User's API key (optional)
   * @returns {Promise<string|null>} The blockchain model ID
   */
  async getModelIdFromName(modelName, apiKey) {
    try {
      // If no API key, just use default mapping
      if (!apiKey) {
        return this.defaultMapping[modelName] || this.defaultMapping['gpt-4'];
      }
      
      // First check Redis cache for specific mapping
      const cachedMapping = await redisClient.get(`${MODEL_MAPPING_KEY_PREFIX}${apiKey}:${modelName}`);
      
      if (cachedMapping) {
        return cachedMapping;
      }
      
      // If not in cache, get all models for the API key
      let modelsJson = await redisClient.get(`${API_KEY_MODELS_CACHE_KEY}${apiKey}`);
      
      // If no cached models, fetch them
      if (!modelsJson) {
        await this.refreshModelsCache(apiKey);
        modelsJson = await redisClient.get(`${API_KEY_MODELS_CACHE_KEY}${apiKey}`);
      }
      
      if (modelsJson) {
        const models = JSON.parse(modelsJson);
        
        // Try to find a model with matching name
        for (const model of models) {
          // Try to match based on the name field - this depends on the blockchain response format
          const modelName = model.Name || model.name;
          if (modelName && modelName.toLowerCase() === modelName.toLowerCase()) {
            // Store in cache for future use
            const modelId = model.Id || model.id;
            await redisClient.set(
              `${MODEL_MAPPING_KEY_PREFIX}${apiKey}:${modelName}`,
              modelId,
              'EX',
              CACHE_TTL
            );
            return modelId;
          }
        }
        
        // If no exact match, use the first model
        if (models.length > 0) {
          const firstModelId = models[0].Id || models[0].id;
          await redisClient.set(
            `${MODEL_MAPPING_KEY_PREFIX}${apiKey}:${modelName}`,
            firstModelId,
            'EX',
            CACHE_TTL
          );
          return firstModelId;
        }
      }
      
      // If not in blockchain models, check default mapping
      if (this.defaultMapping[modelName]) {
        return this.defaultMapping[modelName];
      }
      
      // Fall back to default if no match
      return this.defaultMapping[process.env.DEFAULT_MODEL] || this.defaultMapping['gpt-4'];
    } catch (error) {
      logger.error(`Failed to get model ID for ${modelName}:`, error);
      return this.defaultMapping[process.env.DEFAULT_MODEL] || this.defaultMapping['gpt-4'];
    }
  }
  
  /**
   * Get OpenAI-compatible model name from blockchain model ID
   * @param {string} modelId - The blockchain model ID
   * @param {string} apiKey - User's API key (optional)
   * @returns {Promise<string>} The OpenAI-compatible model name
   */
  async getModelNameFromId(modelId, apiKey) {
    try {
      // If we have an API key, check the cache for that key
      if (apiKey) {
        const modelsJson = await redisClient.get(`${API_KEY_MODELS_CACHE_KEY}${apiKey}`);
        
        if (modelsJson) {
          const models = JSON.parse(modelsJson);
          
          // Try to find model in the list
          for (const model of models) {
            const id = model.Id || model.id;
            if (id === modelId) {
              // Map model name to OpenAI-style name if needed
              const name = model.Name || model.name;
              return this.mapToOpenAIName(name);
            }
          }
        }
      }
      
      // If not found with API key or no API key provided, check reverse mapping
      for (const [name, id] of Object.entries(this.defaultMapping)) {
        if (id === modelId) {
          return name;
        }
      }
      
      // Default fallback
      return process.env.DEFAULT_MODEL || 'gpt-4';
    } catch (error) {
      logger.error(`Failed to get model name for ${modelId}:`, error);
      return process.env.DEFAULT_MODEL || 'gpt-4';
    }
  }
  
  /**
   * Map native model name to OpenAI-compatible name
   * @param {string} nativeName - The native model name
   * @returns {string} The OpenAI-compatible model name
   * @private
   */
  mapToOpenAIName(nativeName) {
    if (!nativeName) return 'gpt-4';
    
    const lowerName = nativeName.toLowerCase();
    
    // Common model mappings
    if (lowerName.includes('llama2:7b') || lowerName.includes('mistral-7b')) {
      return 'gpt-3.5-turbo';
    }
    
    if (lowerName.includes('llama2:70b') || lowerName.includes('claude-3') || 
        lowerName.includes('mixtral') || lowerName.includes('llama3')) {
      return 'gpt-4';
    }
    
    // Fallback mappings
    const nameMapping = {
      'llama2': 'gpt-3.5-turbo',
      'claude-3-5-sonnet': 'gpt-4',
      'gpt-4o-mini': 'gpt-3.5-turbo',
      'gpt-4o': 'gpt-4-turbo',
    };
    
    return nameMapping[lowerName] || nativeName;
  }
  
  /**
   * List all available models in OpenAI-compatible format
   * @param {string} apiKey - User's API key
   * @returns {Promise<Array>} List of model objects
   */
  async listAvailableModels(apiKey) {
    try {
      // If no API key, just use default mapping
      if (!apiKey) {
        return Object.keys(this.defaultMapping).map(name => ({
          id: this.defaultMapping[name],
          openai_name: name
        }));
      }
      
      // Get models from cache or refresh if needed
      let modelsJson = await redisClient.get(`${API_KEY_MODELS_CACHE_KEY}${apiKey}`);
      
      if (!modelsJson) {
        await this.refreshModelsCache(apiKey);
        modelsJson = await redisClient.get(`${API_KEY_MODELS_CACHE_KEY}${apiKey}`);
      }
      
      if (!modelsJson || JSON.parse(modelsJson).length === 0) {
        // If still no models, return default mapping
        return Object.keys(this.defaultMapping).map(name => ({
          id: this.defaultMapping[name],
          openai_name: name
        }));
      }
      
      const models = JSON.parse(modelsJson);
      
      // Format models in OpenAI-compatible way
      return models.map(model => {
        const name = model.Name || model.name;
        const openaiName = this.mapToOpenAIName(name);
        
        return {
          id: model.Id || model.id,
          openai_name: openaiName,
          native_name: name,
          fee: model.Fee || model.fee || 0
        };
      });
    } catch (error) {
      logger.error('Failed to list available models:', error);
      
      // Return default mapping as fallback
      return Object.keys(this.defaultMapping).map(name => ({
        id: this.defaultMapping[name],
        openai_name: name
      }));
    }
  }
}

module.exports = new ModelMappingService(); 