const express = require('express');
const router = express.Router();
const { authenticate } = require('../middleware/auth');
const keyVaultService = require('../services/key-vault-service');
const proxyRouterClient = require('../services/proxy-router-client');
const { createError } = require('../utils/error-utils');
const logger = require('../utils/logger');
const userService = require('../services/user-service');

// Register a new user
router.post('/register', async (req, res) => {
  try {
    const { name, email, password } = req.body;
    
    // Validate input
    if (!name || !email || !password) {
      return res.status(400).json(createError('Name, email, and password are required', 'validation_error'));
    }
    
    // Create user
    const user = await userService.createUser({ name, email, password });
    
    // Generate API key for the new user
    const apiKey = await userService.generateApiKey(user.id);
    
    // Return user and API key
    res.status(201).json({
      user,
      api_key: apiKey.key
    });
  } catch (error) {
    if (error.message === 'Email already in use') {
      return res.status(409).json(createError(error.message, 'email_conflict'));
    }
    
    logger.error('Error during registration:', error);
    res.status(500).json(createError('Registration failed', 'server_error'));
  }
});

// Login user
router.post('/login', async (req, res) => {
  try {
    const { email, password } = req.body;
    
    // Validate input
    if (!email || !password) {
      return res.status(400).json(createError('Email and password are required', 'validation_error'));
    }
    
    // Authenticate user
    const result = await userService.authenticateUser(email, password);
    
    if (!result.success) {
      return res.status(401).json(createError('Invalid email or password', 'authentication_error'));
    }
    
    // Return user and API keys
    res.status(200).json({
      user: result.user,
      api_keys: result.apiKeys
    });
  } catch (error) {
    logger.error('Error during login:', error);
    res.status(500).json(createError('Login failed', 'server_error'));
  }
});

// Get API keys for the authenticated user
router.get('/keys', authenticate, async (req, res) => {
  try {
    // Get user ID from authenticated request
    const userId = req.user.id;
    
    // Get API keys for user
    const apiKeys = await userService.listApiKeys(userId);
    
    res.status(200).json(apiKeys);
  } catch (error) {
    logger.error('Error listing API keys:', error);
    res.status(500).json(createError('Failed to list API keys', 'server_error'));
  }
});

// Create a new API key
router.post('/keys', authenticate, async (req, res) => {
  try {
    // Get user ID from authenticated request
    const userId = req.user.id;
    
    // Generate new API key
    const apiKey = await userService.generateApiKey(userId);
    
    res.status(201).json({
      id: apiKey.id,
      key: apiKey.key,
      created_at: apiKey.createdAt
    });
  } catch (error) {
    logger.error('Error creating API key:', error);
    res.status(500).json(createError('Failed to create API key', 'server_error'));
  }
});

// Revoke an API key
router.delete('/keys/:keyId', authenticate, async (req, res) => {
  try {
    // Get user ID from authenticated request
    const userId = req.user.id;
    
    // Get key ID from params
    const keyId = req.params.keyId;
    
    if (!keyId) {
      return res.status(400).json(createError('API key ID is required', 'validation_error'));
    }
    
    // Revoke API key
    const success = await userService.revokeApiKey(userId, keyId);
    
    if (success) {
      res.status(200).json({ success: true });
    } else {
      res.status(404).json(createError('API key not found', 'not_found'));
    }
  } catch (error) {
    logger.error('Error revoking API key:', error);
    res.status(500).json(createError('Failed to revoke API key', 'server_error'));
  }
});

// Store private key for an API key
router.post('/private-key', authenticate, async (req, res) => {
  try {
    const { privateKey } = req.body;
    const apiKey = req.headers.authorization.split(' ')[1];
    
    if (!privateKey) {
      return res.status(400).json(createError('Private key is required'));
    }
    
    // Validate that the private key is valid by checking the wallet address
    try {
      const walletAddress = await proxyRouterClient.getWalletAddress(privateKey);
      
      if (!walletAddress) {
        return res.status(400).json(createError('Invalid private key'));
      }
      
      // Store private key securely
      const success = await keyVaultService.storePrivateKey(apiKey, privateKey);
      
      if (!success) {
        return res.status(500).json(createError('Failed to store private key'));
      }
      
      res.status(200).json({
        success: true,
        wallet_address: walletAddress
      });
    } catch (error) {
      logger.error('Error validating private key:', error);
      return res.status(400).json(createError('Invalid private key format'));
    }
  } catch (error) {
    logger.error('Error storing private key:', error);
    res.status(500).json(createError('Internal server error'));
  }
});

// Check if private key exists for API key
router.get('/private-key/status', authenticate, async (req, res) => {
  try {
    const apiKey = req.headers.authorization.split(' ')[1];
    
    // Check if private key exists
    const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
    
    res.status(200).json({
      has_private_key: !!hasPrivateKey
    });
  } catch (error) {
    logger.error('Error checking private key status:', error);
    res.status(500).json(createError('Internal server error'));
  }
});

// Delete private key for API key
router.delete('/private-key', authenticate, async (req, res) => {
  try {
    const apiKey = req.headers.authorization.split(' ')[1];
    
    // Delete private key
    const success = await keyVaultService.deletePrivateKey(apiKey);
    
    if (!success) {
      return res.status(500).json(createError('Failed to delete private key'));
    }
    
    res.status(200).json({
      success: true
    });
  } catch (error) {
    logger.error('Error deleting private key:', error);
    res.status(500).json(createError('Internal server error'));
  }
});

// Approve MOR token spending for the contract
router.post('/approve-spending', authenticate, async (req, res) => {
  try {
    const { amount } = req.body;
    const apiKey = req.headers.authorization.split(' ')[1];
    
    if (!amount || isNaN(parseFloat(amount))) {
      return res.status(400).json(createError('Valid amount is required'));
    }
    
    // Check if private key exists
    const hasPrivateKey = await keyVaultService.getPrivateKey(apiKey);
    
    if (!hasPrivateKey) {
      return res.status(400).json(createError(
        'No private key associated with this API key. Please register your private key first.',
        'missing_private_key'
      ));
    }
    
    // Approve spending
    const result = await proxyRouterClient.approveMorSpending(apiKey, parseFloat(amount));
    
    res.status(200).json({
      success: true,
      transaction: result
    });
  } catch (error) {
    logger.error('Error approving token spending:', error);
    res.status(500).json(createError(`Failed to approve token spending: ${error.message}`));
  }
});

// Refresh tokens
router.post('/refresh', (req, res) => {
  // This will be implemented later
  res.status(501).json({ error: { message: 'Not implemented yet' } });
});

// Get current user profile
router.get('/me', authenticate, (req, res) => {
  // This will be implemented later
  res.status(501).json({ error: { message: 'Not implemented yet' } });
});

// Update user profile
router.put('/me', authenticate, (req, res) => {
  // This will be implemented later
  res.status(501).json({ error: { message: 'Not implemented yet' } });
});

// Logout user
router.post('/logout', authenticate, (req, res) => {
  // This will be implemented later
  res.status(501).json({ error: { message: 'Not implemented yet' } });
});

module.exports = router; 