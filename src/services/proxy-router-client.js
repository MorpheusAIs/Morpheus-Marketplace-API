const axios = require('axios');
const logger = require('../utils/logger');
const keyVaultService = require('./key-vault-service');
const { ServiceError } = require('../utils/error-utils');
const fs = require('fs');
const { ValidationError } = require('../utils/error-utils');

/**
 * Proxy Router Client
 * Handles all interactions with the Morpheus-Lumerin-Node proxy-router
 * Implements the key rotation approach to support multiple users
 */
class ProxyRouterClient {
  constructor() {
    this.proxyUrl = process.env.PROXY_ROUTER_URL || 'http://localhost:8082';
    this.defaultTimeout = parseInt(process.env.PROXY_ROUTER_TIMEOUT) || 30000; // 30 seconds
    
    // Basic auth credentials for proxy router
    this.username = process.env.PROXY_ROUTER_USERNAME || 'admin';
    this.password = process.env.PROXY_ROUTER_PASSWORD || 'admin';
    
    // Create basic auth header
    const basicAuth = Buffer.from(`${this.username}:${this.password}`).toString('base64');
    
    // Initialize axios instance with default config
    this.httpClient = axios.create({
      baseURL: this.proxyUrl,
      timeout: this.defaultTimeout,
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Authorization': `Basic ${basicAuth}`
      }
    });
    
    // Add response interceptor for error handling
    this.httpClient.interceptors.response.use(
      response => response,
      error => {
        this.handleApiError(error);
        return Promise.reject(error);
      }
    );
  }
  
  /**
   * Get the blockchain wallet address for a private key
   * @param {string} privateKey - User's private key
   * @returns {Promise<string>} Wallet address
   */
  async getWalletAddress(privateKey) {
    try {
      if (!privateKey) {
        throw new ValidationError('Private key is required');
      }
      
      // Use the callWithPrivateKey method which properly handles the private key
      const response = await this.callWithPrivateKey(
        privateKey,
        'GET',
        '/wallet',
        null
      );
      
      return response.address;
    } catch (error) {
      logger.error('Error getting wallet address:', error);
      throw new ServiceError('Failed to get wallet address');
    }
  }
  
  /**
   * Approve the proxy router to spend tokens
   * @param {string} privateKey - User's private key
   * @param {string} spenderAddress - Address to approve
   * @param {string} amount - Amount to approve
   * @returns {Promise<Object>} Approval transaction result
   */
  async approveSpending(privateKey, spenderAddress, amount) {
    try {
      logger.debug(`Approving spending for ${spenderAddress} with amount ${amount}`);
      const data = {};
      const response = await this.callWithPrivateKey(
        privateKey,
        'POST',
        `/blockchain/approve?spender=${spenderAddress}&amount=${amount}`,
        data
      );
      
      return response;
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Get available models from the blockchain
   * @param {string} privateKey - User's private key
   * @returns {Promise<Array>} List of available models
   */
  async getAvailableModels(privateKey) {
    logger.debug('Getting available models from blockchain');
    try {
      const response = await this.callWithPrivateKey(
        privateKey,
        'GET',
        '/blockchain/models',
        null
      );
      
      return response;
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Get models from the blockchain - this is the method called by ModelMappingService
   * @param {string} privateKey - User's private key
   * @returns {Promise<Array>} List of models from the blockchain
   */
  async getBlockchainModels(privateKey) {
    try {
      const response = await this.getAvailableModels(privateKey);
      return response.models || [];
    } catch (error) {
      logger.error('Error getting blockchain models:', error);
      return [];
    }
  }
  
  /**
   * Create a new model session on the blockchain
   * @param {string} privateKey - User's private key
   * @param {string} modelId - ID of the model to use
   * @param {number} maxCredits - Maximum credits to use for the session
   * @returns {Promise<Object>} Session details
   */
  async createModelSession(privateKey, modelId, maxCredits) {
    try {
      logger.debug(`Creating model session for model ${modelId} with max credits ${maxCredits}`);
      const data = {
        max_credits: maxCredits
      };
      
      const response = await this.callWithPrivateKey(
        privateKey,
        'POST',
        `/blockchain/models/${modelId}/session`,
        data
      );
      
      return response;
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Get session details from the blockchain
   * @param {string} privateKey - User's private key
   * @param {string} sessionId - ID of the session to query
   * @returns {Promise<Object>} Session details
   */
  async getSessionDetails(privateKey, sessionId) {
    logger.debug(`Getting session details for session ${sessionId}`);
    try {
      const response = await this.callWithPrivateKey(
        privateKey,
        'GET',
        `/blockchain/sessions/${sessionId}`,
        null
      );
      
      return response;
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Close a model session on the blockchain
   * @param {string} privateKey - User's private key
   * @param {string} sessionId - ID of the session to close
   * @returns {Promise<Object>} Close session result
   */
  async closeModelSession(privateKey, sessionId) {
    logger.debug(`Closing model session ${sessionId}`);
    try {
      await this.callWithPrivateKey(
        privateKey,
        'POST',
        `/blockchain/sessions/${sessionId}/close`,
        {}
      );
      
      return { success: true };
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Make a chat completion request through the proxy-router
   * @param {string} privateKey - User's private key
   * @param {string} sessionId - Active session ID
   * @param {Array} messages - Chat messages
   * @param {Object} options - Additional options for the chat completion
   * @returns {Promise<Object>} Chat completion response
   */
  async chatCompletion(privateKey, sessionId, messages, options = {}) {
    try {
      logger.debug(`Making chat completion request for session ${sessionId}`);
      const promptData = {
        model: options.model || 'gpt-4',
        messages,
        temperature: options.temperature || 0.7,
        max_tokens: options.maxTokens || 1000,
        ...options
      };

      // Special case for handling chat completions
      if (options.stream) {
        // Streaming not supported in this sample
        throw new Error('Streaming is not supported yet');
      } else {
        // Handle regular response
        const response = await this.callWithPrivateKey(
          privateKey,
          'POST',
          '/v1/chat/completions',
          promptData
        );
        
        return response;
      }
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Execute a proxy router API call with a specific private key
   * @param {string} privateKey - Private key to use for the transaction
   * @param {string} method - HTTP method to use
   * @param {string} endpoint - API endpoint to call
   * @param {Object} data - Request payload
   * @returns {Promise<Object>} API response data
   */
  async callWithPrivateKey(privateKey, method, endpoint, data = {}) {
    try {
      if (!privateKey) {
        throw new ValidationError('Private key is required');
      }

      // Make sure private key is treated as a string to prevent Go type conversion issues
      privateKey = String(privateKey);
      
      // Create basic auth header
      const basicAuth = Buffer.from(`${this.username}:${this.password}`).toString('base64');
      
      const requestConfig = {
        method,
        url: `${this.proxyUrl}${endpoint}`,
        headers: {
          'Content-Type': 'application/json',
          'X-Private-Key': privateKey,
          'Authorization': `Basic ${basicAuth}`
        },
        data: method !== 'GET' ? data : undefined,
        params: method === 'GET' ? data : undefined
      };

      logger.debug(`Making ${method} request to ${endpoint}`);
      const response = await axios(requestConfig);
      return response.data;
    } catch (error) {
      this.handleApiError(error);
      throw error;
    }
  }
  
  /**
   * Handle API errors from the proxy router
   * @param {Error} error - Axios error
   * @private
   */
  handleApiError(error) {
    if (error.response) {
      // The request was made and the server responded with an error
      logger.error('Proxy router error response:', {
        status: error.response.status,
        data: error.response.data
      });
    } else if (error.request) {
      // The request was made but no response was received
      logger.error('No response from proxy router:', error.request);
    } else {
      // Something happened in setting up the request
      logger.error('Error setting up proxy router request:', error.message);
    }
  }
}

module.exports = new ProxyRouterClient(); 