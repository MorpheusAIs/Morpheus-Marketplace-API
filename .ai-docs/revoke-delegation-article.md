Opens in a new windowOpens an external websiteOpens an external website in a new window

Close this dialog

This website utilizes technologies such as cookies to enable essential site functionality, as well as for analytics, personalization, and marketing. To learn more, view the following link: [Cookie Policy](https://consensys.io/privacy-policy/cookies)

Manage Preferences

Close Cookie Preferences

- [Developers](https://metamask.io/news/developers#latest)
- [Ecosystem](https://metamask.io/news/ecosystem#latest)

- Copy link


# Revoke.Delegate: Automating Token Approval Revocation with MetaMask's Delegation Toolkit

Learn how Revoke.delegate automates token approval revocations to protect users during exploits.

- [![Kingsley Okonkwo](https://images.ctfassets.net/clixtyxoaeas/6bBSuI9BBS15l2px9fjZ6D/20f83b7947a9f3af2e9450c7937e2a33/Kingsley_Okonkwo.png?w=3840&q=75&fm=avif)\\
\\
Kingsley Okonkwo](https://metamask.io/news/kingsley-okonkwo)

3 min read

January 16, 2025

![Revoke.Delegate: Automating Token Approval Revocation with MetaMask's Delegation Toolkit](https://images.ctfassets.net/clixtyxoaeas/4OujinhbaB7b0y3LIR4dVP/c203306aab4a6a92dc4b359e84848071/Revoke-Delegate.png?w=3840&q=75&fm=avif)

- Copy link


```
Token smart contracts include an approve() function that allows another address to spend tokens on behalf of the token holder. This is a core part of the smart contract ecosystem today; without it, many DeFi applications would not be possible. For example, trading tokens on a decentralized exchange like Uniswap needs token approval before a swap can happen.
```

```
However, token approvals come with significant risks. Granting a smart contract permission to spend tokens means it can do so at any time. If a contract is hacked or contains malicious code, attackers can exploit these approvals to steal tokens.
```

```
Existing solutions like Revoke.cash track wallet approvals, and allow on-demand revocation. However, they rely on a manual process that must be done before or during an exploit, which is not always practical. Revoke.delegate – a hackathon project built at EthGlobal '24, offers a more efficient solution by automatically revoking approvals during an exploit, eliminating the need for constant monitoring or manual action. Here’s how the team achieved this.
```

## Challenge: delegating wallet permissions while preserving privacy and ownership

```
Users must delegate specific wallet permissions to Revoke.delegate to enable automatic token approval revocation during an exploit. In this context, delegation means a smart contract account (the delegator) grants specific permissions to another smart contract or externally owned account (the delegatee) to perform defined actions under clearly established rules and conditions.
```

```
This process allows Revoke.delegate to securely identify affected token addresses, verify predefined conditions, and revoke approvals by calling the approve() or setApprovalForAll() functions to set allowances to zero.
```

```
The primary challenge involves maintaining privacy and self-ownership principles, ensuring users avoid sharing private keys or forfeiting control of their wallets by granting ambiguous permissions. The team also prioritized compatibility with the existing wallet ecosystem to facilitate seamless adoption.
```

## Solution: securely delegate and manage wallet permissions with the Delegation Toolkit (DTK)

```
Integrating with the Delegation Toolkit (DTK) enabled Revoke.delegate to allow users to delegate wallet permissions while maintaining full self-ownership. Whenever an exploit is reported, Revoke.delegate securely retrieves the user delegations from the Delegation Storage managed by DTK and uses them to revoke token approvals for affected wallets. Here’s a high-level overview of the process:
```

1. ```
A user connects their wallet to view all existing token approvals.
```

2. ```embed_richtext-code-inline__kBhjn
They choose to delegate wallet permissions. Behind the scenes, the createRootDelegation() function is called, specifying the Revoke.delegate smart contract as the delegatee.
```

3. ```embed_richtext-code-inline__kBhjn
The required permissions are defined using the addCaveats() function, specifying the approve() method, such as .addCaveat("allowedMethods", ["approve(address,uint256)"]).build().
```

4. ```
The user signs the new delegation, which is then stored securely in the Delegation Storage managed by DKT.
```

5. ```embed_richtext-code-inline__kBhjn
In the event of an exploit, Revoke.delegate retrieves all stored delegations, verifies conditions, and revokes token approvals for affected wallets by calling the approve() or setApprovalForAll() functions to set allowances to zero—all without requiring user action.
```


```
Explore the docs to learn more about how the Delegation Toolkit streamlines these processes.
```

```
"The Delegation Toolkit helped us ensure users could delegate their wallet permissions without relinquishing ownership." – Ayush & Aashish, Builders of Revoke.delegate and EthGlobal ‘24 Hackathon Winners
```

```
By leveraging DTK, the Revoke.delegate team successfully automated token approval revocations, even in cases where users were offline or unaware of an ongoing exploit. Furthermore, since the toolkit natively supports ERC-7579, the team developed a solution compatible with leading wallets in the ecosystem, including Safe, Kernel, and Biconomy Nexus.
```
