const { v4: uuidv4 } = require('uuid');
const bcrypt = require('bcrypt');
const logger = require('../utils/logger');

// This is a placeholder service that would be replaced with a real database implementation
// For now, it just stores users and API keys in memory
class UserService {
  constructor() {
    this.users = new Map();
    this.apiKeys = new Map();
    
    // Add a test user
    const testUser = {
      id: 'user_1',
      name: 'Test User',
      email: 'test@example.com',
      passwordHash: bcrypt.hashSync('password123', 10),
      createdAt: new Date(),
      updatedAt: new Date()
    };
    
    this.users.set(testUser.id, testUser);
    
    // Add a test API key
    const testApiKey = 'test-api-key';
    this.apiKeys.set(testApiKey, {
      id: 'key_1',
      key: testApiKey,
      userId: testUser.id,
      createdAt: new Date(),
      lastUsed: null
    });
  }
  
  /**
   * Get a user by their API key
   * @param {string} apiKey - The API key to look up
   * @returns {Promise<Object|null>} The user object or null if not found
   */
  async getUserByApiKey(apiKey) {
    try {
      // In a real implementation, this would query the database
      const keyObject = this.apiKeys.get(apiKey);
      
      if (!keyObject) {
        return null;
      }
      
      // Update last used timestamp
      keyObject.lastUsed = new Date();
      
      // Get the associated user
      const user = this.users.get(keyObject.userId);
      
      if (!user) {
        return null;
      }
      
      // Don't return the password hash
      const { passwordHash, ...userWithoutPassword } = user;
      
      return userWithoutPassword;
    } catch (error) {
      logger.error('Error getting user by API key:', error);
      return null;
    }
  }
  
  /**
   * Create a new user
   * @param {Object} userData - User data (name, email, password)
   * @returns {Promise<Object>} The created user
   */
  async createUser(userData) {
    try {
      const { name, email, password } = userData;
      
      // Check if email already exists
      const existingUser = Array.from(this.users.values()).find(u => u.email === email);
      if (existingUser) {
        throw new Error('Email already in use');
      }
      
      // Create new user
      const userId = `user_${uuidv4()}`;
      const now = new Date();
      
      const newUser = {
        id: userId,
        name,
        email,
        passwordHash: await bcrypt.hash(password, 10),
        createdAt: now,
        updatedAt: now
      };
      
      // Store user
      this.users.set(userId, newUser);
      
      // Don't return the password hash
      const { passwordHash, ...userWithoutPassword } = newUser;
      
      return userWithoutPassword;
    } catch (error) {
      logger.error('Error creating user:', error);
      throw error;
    }
  }
  
  /**
   * Generate a new API key for a user
   * @param {string} userId - The user ID
   * @returns {Promise<Object>} The API key object
   */
  async generateApiKey(userId) {
    try {
      // Check if user exists
      if (!this.users.has(userId)) {
        throw new Error('User not found');
      }
      
      // Generate new API key
      const apiKey = `sk-${uuidv4().replace(/-/g, '')}`;
      const keyId = `key_${uuidv4()}`;
      const now = new Date();
      
      const keyObject = {
        id: keyId,
        key: apiKey,
        userId,
        createdAt: now,
        lastUsed: null
      };
      
      // Store API key
      this.apiKeys.set(apiKey, keyObject);
      
      return keyObject;
    } catch (error) {
      logger.error('Error generating API key:', error);
      throw error;
    }
  }
  
  /**
   * List API keys for a user
   * @param {string} userId - The user ID
   * @returns {Promise<Array>} Array of API key objects
   */
  async listApiKeys(userId) {
    try {
      // Check if user exists
      if (!this.users.has(userId)) {
        throw new Error('User not found');
      }
      
      // Find all API keys for this user
      const keys = Array.from(this.apiKeys.values())
        .filter(key => key.userId === userId)
        .map(key => ({
          id: key.id,
          createdAt: key.createdAt,
          lastUsed: key.lastUsed,
          // Only show first and last 4 characters of the key
          key: `${key.key.substring(0, 4)}...${key.key.substring(key.key.length - 4)}`
        }));
      
      return keys;
    } catch (error) {
      logger.error('Error listing API keys:', error);
      throw error;
    }
  }
  
  /**
   * Revoke an API key
   * @param {string} userId - The user ID
   * @param {string} keyId - The key ID to revoke
   * @returns {Promise<boolean>} Success indicator
   */
  async revokeApiKey(userId, keyId) {
    try {
      // Find the key by ID
      let keyToRevoke = null;
      let keyValue = null;
      
      for (const [key, data] of this.apiKeys.entries()) {
        if (data.id === keyId && data.userId === userId) {
          keyToRevoke = data;
          keyValue = key;
          break;
        }
      }
      
      if (!keyToRevoke) {
        throw new Error('API key not found or not owned by user');
      }
      
      // Remove the key
      this.apiKeys.delete(keyValue);
      
      return true;
    } catch (error) {
      logger.error('Error revoking API key:', error);
      throw error;
    }
  }
  
  /**
   * Authenticate a user with email and password
   * @param {string} email - The user's email
   * @param {string} password - The user's password
   * @returns {Promise<Object>} Authentication result with user and API keys
   */
  async authenticateUser(email, password) {
    try {
      // Find user by email
      const user = Array.from(this.users.values()).find(u => u.email === email);
      
      if (!user) {
        return { success: false };
      }
      
      // Compare password
      const passwordMatch = await bcrypt.compare(password, user.passwordHash);
      
      if (!passwordMatch) {
        return { success: false };
      }
      
      // Get API keys for user
      const apiKeys = await this.listApiKeys(user.id);
      
      // Don't return the password hash
      const { passwordHash, ...userWithoutPassword } = user;
      
      return {
        success: true,
        user: userWithoutPassword,
        apiKeys
      };
    } catch (error) {
      logger.error('Error authenticating user:', error);
      throw error;
    }
  }
}

module.exports = new UserService(); 