const logger = require('../utils/logger');
const { createError } = require('../utils/error-utils');
const userService = require('../services/user-service');

/**
 * Placeholder for user service (to be implemented)
 * This is just a stub for now
 */
const userServiceStub = {
  getUserByApiKey: async (apiKey) => {
    // This will be replaced with actual implementation
    if (apiKey === 'test-api-key') {
      return {
        id: 'test-user-id',
        name: 'Test User',
        email: 'test@example.com'
      };
    }
    return null;
  }
};

/**
 * Middleware to authenticate requests using API key
 * @param {Object} req - Express request object
 * @param {Object} res - Express response object
 * @param {Function} next - Express next function
 */
const authenticate = async (req, res, next) => {
  try {
    // Extract API key from Authorization header
    const authHeader = req.headers.authorization;
    
    if (!authHeader) {
      return res.status(401).json(createError(
        'Missing API key. Please include your API key in the Authorization header using the format: Bearer YOUR_API_KEY',
        'authentication_error'
      ));
    }
    
    // Check if it's Bearer token format
    let apiKey;
    if (authHeader.startsWith('Bearer ')) {
      apiKey = authHeader.substring(7, authHeader.length);
    } else {
      return res.status(401).json(createError(
        'Invalid API key format. Please use the format: Bearer YOUR_API_KEY',
        'authentication_error'
      ));
    }
    
    // Validate API key
    const user = await userService.getUserByApiKey(apiKey);
    
    if (!user) {
      return res.status(401).json(createError(
        'Invalid API key provided.',
        'authentication_error'
      ));
    }
    
    // Attach user to request
    req.user = user;
    
    // Proceed to the next middleware
    next();
  } catch (error) {
    logger.error('Authentication error:', error);
    return res.status(500).json(createError(
      'An error occurred during authentication.',
      'server_error'
    ));
  }
};

module.exports = {
  authenticate
}; 