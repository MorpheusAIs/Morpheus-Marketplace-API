const { createClient } = require('redis');
const logger = require('./logger');

// Create Redis client
const redisClient = createClient({
  socket: {
    host: process.env.REDIS_HOST || 'localhost',
    port: process.env.REDIS_PORT || 6379
  },
  username: process.env.REDIS_USERNAME || undefined,
  password: process.env.REDIS_PASSWORD || undefined,
  prefix: process.env.REDIS_PREFIX || 'morpheus:',
});

// Handle Redis connection events
redisClient.on('connect', () => {
  logger.info('Redis client connected');
});

redisClient.on('error', (err) => {
  logger.error('Redis client error:', err);
});

redisClient.on('reconnecting', () => {
  logger.info('Redis client reconnecting');
});

// Initialize connection
(async () => {
  try {
    await redisClient.connect();
  } catch (err) {
    logger.error('Failed to connect to Redis:', err);
  }
})();

// Graceful shutdown
process.on('SIGTERM', async () => {
  logger.info('SIGTERM received, closing Redis connection');
  await redisClient.quit();
});

process.on('SIGINT', async () => {
  logger.info('SIGINT received, closing Redis connection');
  await redisClient.quit();
});

module.exports = redisClient; 