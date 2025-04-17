const { v4: uuidv4 } = require('uuid');
const logger = require('../utils/logger');
const redisClient = require('../utils/redis');
const proxyRouterClient = require('./proxy-router-client');
const { ServiceError } = require('../utils/error-utils');

// Session cache keys
const SESSION_KEY_PREFIX = 'session:';
const USER_SESSIONS_KEY_PREFIX = 'user:sessions:';
const API_KEY_SESSION_MAPPING = 'api_key:session:';
const SESSION_TIMEOUT = 3600; // 1 hour in seconds

/**
 * Session Service
 * Handles session lifecycle management between the API and blockchain
 */
class SessionService {
  constructor() {
    // Setup cleanup for expired sessions
    setInterval(() => this.cleanupExpiredSessions(), 15 * 60 * 1000); // every 15 minutes
  }

  /**
   * Create a new session for a model
   * @param {string} apiKey - The user's API key
   * @param {string} userId - The user identifier
   * @param {string} modelId - The model identifier on blockchain
   * @param {number} durationSeconds - Session duration in seconds
   * @param {boolean} directPayment - Whether to use direct payment or stake
   * @param {boolean} enableFailover - Whether to enable failover to other providers
   * @returns {Promise<Object>} The created session
   */
  async createSession(apiKey, userId, modelId, durationSeconds = 600, directPayment = true, enableFailover = true) {
    try {
      logger.info(`Creating session for user ${userId} on model ${modelId}`, {
        durationSeconds,
        directPayment,
        enableFailover
      });

      // Call proxy router to create a session
      const sessionResponse = await proxyRouterClient.createBlockchainSession(
        apiKey, 
        modelId, 
        durationSeconds, 
        directPayment, 
        enableFailover
      );
      
      const sessionId = sessionResponse.sessionId;
      
      // Get session details
      const sessionDetails = await proxyRouterClient.getSessionDetails(apiKey, sessionId);
      
      // Store session in cache
      const sessionData = {
        id: sessionId,
        userId,
        apiKey,
        modelId,
        createdAt: Date.now(),
        expiresAt: Date.now() + (durationSeconds * 1000),
        directPayment,
        enableFailover,
        provider: sessionDetails.provider || 'unknown',
        status: 'active'
      };
      
      await this.cacheSession(sessionData);
      
      return sessionData;
    } catch (error) {
      logger.error('Failed to create session:', error);
      throw new ServiceError(`Failed to create session: ${error.message}`);
    }
  }

  /**
   * Get session details from the blockchain
   * @param {string} apiKey - The user's API key
   * @param {string} sessionId - The session identifier
   * @returns {Promise<Object>} Session details
   */
  async getSessionDetails(apiKey, sessionId) {
    try {
      return await proxyRouterClient.getSessionDetails(apiKey, sessionId);
    } catch (error) {
      logger.error(`Failed to get session details for ${sessionId}:`, error);
      throw new ServiceError(`Failed to get session details: ${error.message}`);
    }
  }

  /**
   * Close a session
   * @param {string} apiKey - The user's API key
   * @param {string} sessionId - The session identifier
   * @returns {Promise<boolean>} Success indicator
   */
  async closeSession(apiKey, sessionId) {
    try {
      logger.info(`Closing session ${sessionId}`);
      
      // Close session on blockchain
      await proxyRouterClient.closeBlockchainSession(apiKey, sessionId);
      
      // Update cache
      const session = await this.getSession(sessionId);
      if (session) {
        session.status = 'closed';
        await this.cacheSession(session);
      }
      
      return true;
    } catch (error) {
      logger.error(`Failed to close session ${sessionId}:`, error);
      throw new ServiceError(`Failed to close session: ${error.message}`);
    }
  }

  /**
   * Get a session by ID
   * @param {string} sessionId - The session identifier
   * @returns {Promise<Object|null>} Session object or null if not found
   */
  async getSession(sessionId) {
    try {
      const sessionJson = await redisClient.get(`${SESSION_KEY_PREFIX}${sessionId}`);
      
      if (!sessionJson) {
        return null;
      }
      
      return JSON.parse(sessionJson);
    } catch (error) {
      logger.error(`Failed to get session ${sessionId}:`, error);
      return null;
    }
  }

  /**
   * Get all active sessions for a user
   * @param {string} userId - The user identifier
   * @returns {Promise<Array>} Array of session objects
   */
  async getUserSessions(userId) {
    try {
      const sessionIds = await redisClient.sMembers(`${USER_SESSIONS_KEY_PREFIX}${userId}`);
      
      if (!sessionIds || sessionIds.length === 0) {
        return [];
      }
      
      const sessions = [];
      
      for (const sessionId of sessionIds) {
        const session = await this.getSession(sessionId);
        
        if (session && session.status === 'active') {
          sessions.push(session);
        }
      }
      
      return sessions;
    } catch (error) {
      logger.error(`Failed to get sessions for user ${userId}:`, error);
      return [];
    }
  }
  
  /**
   * Get active session for a given API key and model ID
   * @param {string} apiKey - The API key
   * @param {string} modelId - The model ID
   * @returns {Promise<Object|null>} Session object or null if not found
   */
  async getSessionForApiKeyAndModel(apiKey, modelId) {
    try {
      const sessionId = await redisClient.get(`${API_KEY_SESSION_MAPPING}${apiKey}:${modelId}`);
      
      if (!sessionId) {
        return null;
      }
      
      const session = await this.getSession(sessionId);
      
      if (!session || session.status !== 'active') {
        // Clean up mapping if session is not active
        await redisClient.del(`${API_KEY_SESSION_MAPPING}${apiKey}:${modelId}`);
        return null;
      }
      
      // Verify the session is not expired
      if (session.expiresAt < Date.now()) {
        session.status = 'expired';
        await this.cacheSession(session);
        await redisClient.del(`${API_KEY_SESSION_MAPPING}${apiKey}:${modelId}`);
        return null;
      }
      
      return session;
    } catch (error) {
      logger.error(`Failed to get session for API key and model:`, error);
      return null;
    }
  }

  /**
   * Store session in cache
   * @param {Object} session - Session object
   * @returns {Promise<void>}
   */
  async cacheSession(session) {
    try {
      // Cache session details
      await redisClient.set(
        `${SESSION_KEY_PREFIX}${session.id}`,
        JSON.stringify(session),
        'EX',
        SESSION_TIMEOUT
      );
      
      // Add to user's session set
      await redisClient.sAdd(`${USER_SESSIONS_KEY_PREFIX}${session.userId}`, session.id);
      await redisClient.expire(`${USER_SESSIONS_KEY_PREFIX}${session.userId}`, SESSION_TIMEOUT);
      
      // Map API key to session for fast lookups
      if (session.status === 'active') {
        await redisClient.set(
          `${API_KEY_SESSION_MAPPING}${session.apiKey}:${session.modelId}`,
          session.id,
          'EX',
          SESSION_TIMEOUT
        );
      }
    } catch (error) {
      logger.error(`Failed to cache session ${session.id}:`, error);
    }
  }

  /**
   * Clean up expired sessions
   * @returns {Promise<void>}
   */
  async cleanupExpiredSessions() {
    try {
      const now = Date.now();
      const pattern = `${SESSION_KEY_PREFIX}*`;
      
      // Scan Redis for session keys
      let cursor = '0';
      do {
        const reply = await redisClient.scan(cursor, 'MATCH', pattern, 'COUNT', 100);
        cursor = reply[0];
        const keys = reply[1];
        
        for (const key of keys) {
          const sessionJson = await redisClient.get(key);
          
          if (sessionJson) {
            const session = JSON.parse(sessionJson);
            
            if (session.expiresAt < now && session.status === 'active') {
              // Session has expired but still marked as active
              logger.info(`Marking expired session: ${session.id}`);
              session.status = 'expired';
              await this.cacheSession(session);
              
              // Remove API key mapping
              await redisClient.del(`${API_KEY_SESSION_MAPPING}${session.apiKey}:${session.modelId}`);
              
              // Try to close on blockchain
              try {
                await proxyRouterClient.closeBlockchainSession(session.apiKey, session.id);
              } catch (error) {
                logger.error(`Failed to close expired session on blockchain: ${session.id}`, error);
              }
            }
          }
        }
      } while (cursor !== '0');
    } catch (error) {
      logger.error('Failed to clean up expired sessions:', error);
    }
  }

  /**
   * Send a prompt through an active session
   * @param {string} apiKey - The user's API key
   * @param {string} sessionId - The session identifier
   * @param {Object} promptData - The prompt data in OpenAI format
   * @param {Function} streamCallback - Callback for streaming responses
   * @returns {Promise<Object>} Response data
   */
  async sendPrompt(apiKey, sessionId, promptData, streamCallback = null) {
    try {
      const session = await this.getSession(sessionId);
      if (!session || session.status !== 'active') {
        throw new ServiceError(`Invalid or inactive session: ${sessionId}`);
      }
      
      // Check if session is expired according to our local cache
      if (session.expiresAt < Date.now()) {
        session.status = 'expired';
        await this.cacheSession(session);
        throw new ServiceError(`Session has expired: ${sessionId}`);
      }
      
      // Send the completion request
      return await proxyRouterClient.sendChatCompletion(
        apiKey,
        sessionId,
        promptData,
        streamCallback
      );
    } catch (error) {
      logger.error(`Failed to send prompt for session ${sessionId}:`, error);
      throw new ServiceError(`Failed to send prompt: ${error.message}`);
    }
  }
}

module.exports = new SessionService(); 