const crypto = require('crypto');
const { v4: uuidv4 } = require('uuid');
const logger = require('../utils/logger');
const redisClient = require('../utils/redis');

// Redis keys
const API_KEY_TO_PRIVATE_KEY_PREFIX = 'user:private_key:';
const PRIVATE_KEY_USAGE_PREFIX = 'private_key:usage:';
const KEY_TTL = 3600; // 1 hour

// In-memory cache for currently loaded keys
const activeKeys = new Map();

/**
 * Key Vault Service
 * Securely manages user private keys with encryption and temporary storage
 */
class KeyVaultService {
  constructor() {
    this.encryptionKey = process.env.ENCRYPTION_KEY || 'default-encryption-key-change-in-production';
    
    // Setup cleanup interval (every 15 minutes)
    setInterval(() => this.cleanupUnusedKeys(), 15 * 60 * 1000);
  }
  
  /**
   * Store a private key for a user's API key
   * @param {string} apiKey - User's API key
   * @param {string} privateKey - Ethereum private key (without 0x prefix)
   * @returns {Promise<boolean>} Success indicator
   */
  async storePrivateKey(apiKey, privateKey) {
    try {
      // Check if apiKey or privateKey is undefined
      if (!apiKey || !privateKey) {
        logger.error('Missing required parameters:', { apiKey: !!apiKey, privateKey: !!privateKey });
        return false;
      }
      
      // Ensure apiKey doesn't still include the Bearer prefix
      if (apiKey.startsWith('Bearer ')) {
        logger.error('API key still includes Bearer prefix, which should be stripped by the route handler');
        apiKey = apiKey.substring(7);
      }
      
      // Use the private key as-is without any prefix handling
      const encryptedKey = this.encryptData(privateKey);
      
      // Store in Redis
      await redisClient.set(
        `${API_KEY_TO_PRIVATE_KEY_PREFIX}${apiKey}`, 
        encryptedKey,
        'EX',
        KEY_TTL
      );
      
      logger.info(`Stored private key for API key: ${apiKey.substring(0, 5)}...`);
      return true;
    } catch (error) {
      logger.error('Error storing private key:', error);
      return false;
    }
  }
  
  /**
   * Get a private key for a user's API key
   * @param {string} apiKey - User's API key
   * @returns {Promise<string|null>} The private key or null if not found
   */
  async getPrivateKey(apiKey) {
    try {
      // Ensure apiKey doesn't still include the Bearer prefix
      if (apiKey && apiKey.startsWith('Bearer ')) {
        logger.error('API key still includes Bearer prefix, which should be stripped by the route handler');
        apiKey = apiKey.substring(7);
      }
      
      // First check in-memory cache
      if (activeKeys.has(apiKey)) {
        return activeKeys.get(apiKey);
      }
      
      // Get from Redis
      const encryptedKey = await redisClient.get(`${API_KEY_TO_PRIVATE_KEY_PREFIX}${apiKey}`);
      
      if (!encryptedKey) {
        return null;
      }
      
      // Decrypt the key
      const privateKey = this.decryptData(encryptedKey);
      
      // Cache in memory for faster access
      activeKeys.set(apiKey, privateKey);
      
      // Update usage timestamp
      await redisClient.set(
        `${PRIVATE_KEY_USAGE_PREFIX}${apiKey}`,
        Date.now().toString(),
        'EX',
        KEY_TTL
      );
      
      // Reset TTL on the main key
      await redisClient.expire(`${API_KEY_TO_PRIVATE_KEY_PREFIX}${apiKey}`, KEY_TTL);
      
      return privateKey;
    } catch (error) {
      logger.error('Error retrieving private key:', error);
      return null;
    }
  }
  
  /**
   * Delete a private key for a user's API key
   * @param {string} apiKey - User's API key
   * @returns {Promise<boolean>} Success indicator
   */
  async deletePrivateKey(apiKey) {
    try {
      // Ensure apiKey doesn't still include the Bearer prefix
      if (apiKey && apiKey.startsWith('Bearer ')) {
        logger.error('API key still includes Bearer prefix, which should be stripped by the route handler');
        apiKey = apiKey.substring(7);
      }
      
      // Remove from Redis
      await redisClient.del(`${API_KEY_TO_PRIVATE_KEY_PREFIX}${apiKey}`);
      await redisClient.del(`${PRIVATE_KEY_USAGE_PREFIX}${apiKey}`);
      
      // Remove from memory
      activeKeys.delete(apiKey);
      
      logger.info(`Deleted private key for API key: ${apiKey.substring(0, 5)}...`);
      return true;
    } catch (error) {
      logger.error('Error deleting private key:', error);
      return false;
    }
  }
  
  /**
   * Cleanup unused keys from memory
   * @private
   */
  async cleanupUnusedKeys() {
    try {
      const now = Date.now();
      const unusedThreshold = now - (30 * 60 * 1000); // 30 minutes ago
      
      // Check each key in memory
      for (const [apiKey, _] of activeKeys) {
        const lastUsedStr = await redisClient.get(`${PRIVATE_KEY_USAGE_PREFIX}${apiKey}`);
        
        if (!lastUsedStr || parseInt(lastUsedStr) < unusedThreshold) {
          // Remove from memory if unused for more than 30 minutes
          activeKeys.delete(apiKey);
          logger.debug(`Removed unused key from memory: ${apiKey.substring(0, 5)}...`);
        }
      }
    } catch (error) {
      logger.error('Error cleaning up unused keys:', error);
    }
  }
  
  /**
   * Encrypt data using the vault's encryption key
   * @param {string} data - Data to encrypt
   * @returns {string} Encrypted data
   * @private
   */
  encryptData(data) {
    const iv = crypto.randomBytes(16);
    const cipher = crypto.createCipheriv(
      'aes-256-cbc', 
      Buffer.from(this.encryptionKey.padEnd(32).slice(0, 32)),
      iv
    );
    
    let encrypted = cipher.update(data, 'utf8', 'hex');
    encrypted += cipher.final('hex');
    
    return `${iv.toString('hex')}:${encrypted}`;
  }
  
  /**
   * Decrypt data using the vault's encryption key
   * @param {string} encryptedData - Data to decrypt
   * @returns {string} Decrypted data
   * @private
   */
  decryptData(encryptedData) {
    const [ivHex, encryptedHex] = encryptedData.split(':');
    const iv = Buffer.from(ivHex, 'hex');
    const decipher = crypto.createDecipheriv(
      'aes-256-cbc',
      Buffer.from(this.encryptionKey.padEnd(32).slice(0, 32)),
      iv
    );
    
    let decrypted = decipher.update(encryptedHex, 'hex', 'utf8');
    decrypted += decipher.final('utf8');
    
    return decrypted;
  }
}

module.exports = new KeyVaultService(); 