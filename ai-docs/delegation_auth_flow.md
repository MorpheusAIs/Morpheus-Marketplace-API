# Combined Web2 Login and Web3 Delegation Flow

This document outlines the recommended user authentication and authorization flow for the Morpheus API Gateway, integrating traditional email/password login (Web2) with wallet-based delegation signing (Web3) using the MetaMask Delegation Toolkit.

## Overview

The goal is to allow users to authorize the API Gateway to interact with the Morpheus network (via the `proxy-router`) on their behalf, without the Gateway ever handling their private keys. This is achieved by:

1.  Authenticating the user *to the API Gateway application* using standard Web2 email/password and JWT.
2.  Having the authenticated user *delegate specific permissions* to the Gateway using their Web3 wallet to sign a structured delegation message.

## User Flow Steps

1.  **Web2 Authentication (Login/Register):**
    *   The user interacts with the Gateway's frontend UI.
    *   They register or log in using email and password via the `/auth/register` or `/auth/login` backend endpoints.
    *   The frontend receives a JWT `access_token` and `refresh_token`.
    *   The frontend securely stores these tokens (e.g., in memory, localStorage, or httpOnly cookies).

2.  **Initiate Delegation (Frontend):**
    *   The user, now logged into the Gateway application (identified by the JWT), navigates to an account settings or connection section.
    *   They click a button like "Delegate Permissions" or "Connect & Authorize Wallet".

3.  **Web3 Delegation Creation & Signing (Frontend + Wallet):**
    *   The frontend uses the `@metamask/delegation-toolkit` JavaScript library.
    *   It requests connection to the user's browser wallet (e.g., MetaMask extension).
    *   It constructs the `Delegation` data structure:
        *   `delegate`: Set to the Gateway's predefined public delegate address (`settings.GATEWAY_DELEGATE_ADDRESS`). This address should be fetched from the backend.
        *   `delegator`: The user's smart contract account address (obtained from the connected wallet or managed by the toolkit).
        *   `caveats`: Defines the rules and restrictions for the delegation. This is crucial for security.
            *   *Initial Idea:* Allow specific function calls on the `proxy-router` contract address.
            *   *Refinement:* Consider time limits, spending limits (if applicable), or other relevant constraints based on the Morpheus network interactions.
    *   The frontend uses the toolkit library to request the user sign the structured `Delegation` data via their connected wallet (using EIP-712 standard).

4.  **Submit Signed Delegation to Backend (Frontend -> Backend):**
    *   The frontend receives the complete signed delegation object (original data + signature) from the wallet interaction.
    *   It makes a `POST` request to the backend `/auth/delegation` endpoint.
    *   **Crucially:** The frontend includes the user's JWT `access_token` in the `Authorization: Bearer <token>` header of this request.

5.  **Backend Stores Delegation (Backend):**
    *   The `/auth/delegation` endpoint receives the request.
    *   The `Depends(CurrentUser)` dependency verifies the JWT `access_token`, identifying the logged-in user.
    *   The endpoint logic:
        *   Validates that the `delegate_address` in the received data matches `settings.GATEWAY_DELEGATE_ADDRESS`.
        *   (Optional) Deactivates any previously active delegations for this user if only one should be active.
        *   Calls the appropriate CRUD function (`delegation_crud.create_user_delegation`) to store the `signed_delegation_data` (as JSON or text) in the database, associating it with the authenticated `current_user.id`.
    *   Returns a success response to the frontend.

## Summary of Roles

*   **Web2 Login (Email/Password + JWT):** Authenticates the user *to the API Gateway application itself*. Manages the user's session with the Gateway.
*   **Web3 Delegation (Signed EIP-712 Message):** Authorizes *the Gateway's designated delegate account* to perform specific, restricted actions *on the blockchain/proxy-router* on behalf of the user.
*   **API Key:** Used for subsequent stateless requests to the Gateway's primary API endpoints (e.g., `/chat/completions`). The Gateway uses the API key to identify the user, retrieve their *delegation*, and then use that delegation to interact with the downstream service. 