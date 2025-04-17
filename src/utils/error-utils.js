/**
 * Creates a standardized error response object in OpenAI-compatible format
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
 * Custom error classes
 */
class ValidationError extends Error {
  constructor(message, details = null) {
    super(message);
    this.name = 'ValidationError';
    this.details = details;
    this.statusCode = 400;
  }
}

class AuthenticationError extends Error {
  constructor(message) {
    super(message);
    this.name = 'UnauthorizedError';
    this.statusCode = 401;
  }
}

class PermissionError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ForbiddenError';
    this.statusCode = 403;
  }
}

class NotFoundError extends Error {
  constructor(message) {
    super(message);
    this.name = 'NotFoundError';
    this.statusCode = 404;
  }
}

class RateLimitError extends Error {
  constructor(message) {
    super(message);
    this.name = 'RateLimitError';
    this.statusCode = 429;
  }
}

class ServiceError extends Error {
  constructor(message) {
    super(message);
    this.name = 'ServiceError';
    this.statusCode = 503;
  }
}

module.exports = {
  createError,
  ValidationError,
  AuthenticationError,
  PermissionError,
  NotFoundError,
  RateLimitError,
  ServiceError
};

 