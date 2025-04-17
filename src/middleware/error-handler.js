const logger = require('../utils/logger');

/**
 * Creates a standardized error response object
 * @param {string} message - The error message
 * @param {string} [type='invalid_request_error'] - The error type
 * @param {Object} [details=null] - Additional error details
 * @returns {Object} Formatted error object
 */
const createError = (message, type = 'invalid_request_error', details = null) => {
  const error = {
    error: {
      message,
      type,
      param: null,
      code: null
    }
  };

  if (details) {
    error.error.details = details;
  }

  return error;
};

/**
 * Global error handling middleware
 * @param {Error} err - The error object
 * @param {Object} req - Express request object
 * @param {Object} res - Express response object
 * @param {Function} next - Express next function
 */
const errorHandler = (err, req, res, next) => {
  // Log the error
  logger.error('Error:', {
    message: err.message,
    stack: err.stack,
    path: req.path,
    method: req.method,
    ip: req.ip,
    user: req.user ? req.user.id : 'unauthenticated'
  });

  // Determine status code
  let statusCode = err.statusCode || 500;

  // Handle different types of errors
  if (err.name === 'ValidationError') {
    statusCode = 400;
    return res.status(statusCode).json(createError(
      err.message,
      'validation_error',
      err.details
    ));
  }

  if (err.name === 'UnauthorizedError') {
    statusCode = 401;
    return res.status(statusCode).json(createError(
      'Invalid authentication credentials',
      'authentication_error'
    ));
  }

  if (err.name === 'ForbiddenError') {
    statusCode = 403;
    return res.status(statusCode).json(createError(
      'You do not have permission to perform this action',
      'permission_error'
    ));
  }

  // Default error response
  return res.status(statusCode).json(createError(
    process.env.NODE_ENV === 'production' 
      ? 'An error occurred during the request'
      : err.message,
    'api_error'
  ));
};

module.exports = {
  errorHandler,
  createError
}; 