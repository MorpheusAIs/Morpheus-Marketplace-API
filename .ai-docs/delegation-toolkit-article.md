- [Developers](https://metamask.io/news/developers#latest)

- Copy link


# What is the Delegation Toolkit and what can you build with it?

Learn how the Delegation Toolkit lets you build dapps with flexible and shareable permissions

- [![Kingsley Okonkwo](https://images.ctfassets.net/clixtyxoaeas/6bBSuI9BBS15l2px9fjZ6D/20f83b7947a9f3af2e9450c7937e2a33/Kingsley_Okonkwo.png?w=3840&q=75&fm=avif)\\
\\
Kingsley Okonkwo](https://metamask.io/news/kingsley-okonkwo)

3 Mins

April 2, 2025

![What is the Delegation Toolkit and what can you build with it?](https://images.ctfassets.net/clixtyxoaeas/1AKUOM2zhhp8tRKGWfbwsV/f2f9aad05fde2d92aa8f2e2345d42a4e/25-03-26_What_Exactly_is_the_Delegation_Toolkit.png?w=3840&q=75&fm=avif)

- Copy link


```
Account control on Ethereum has traditionally been rigid. Externally Owned Accounts (EOAs) are simple and widely used but limited in what they can do. Smart Contract Accounts (SCAs) offer far more flexibility, but until recently, they couldn’t operate independently. You still needed an EOA to initiate and pay for every transaction.
```

```
That changed with ERC-4337, as SCAs can now natively support features like gas sponsorship, transaction batching (combining multiple transactions into one), session keys, and chain abstraction, leading to better security, flexibility, and user control.
```

```
Still, EOAs haven’t gone away. And for good reason: not every user finds it useful to have an account that supports arbitrary verification logic. What’s the point of switching to an SCA just yet?
```

```
But how then do we ensure that when they are ready to make the switch—or even temporarily leverage one or more ERC-4337 account features—they’re not forced into creating a new account type from scratch? How do we enable EOAs to tap into the features of an SCA for specific transactions without giving up their familiar setup?
```

```
This is where EIP-7702 (Set-EOA-account-code) comes into play.
```

```
As part of the upcoming Pectra upgrade, EIP-7702 will let EOAs temporarily behave like SCAs by delegating control to smart contract code that executes directly from their addresses. That means users won’t have to migrate to new accounts to enjoy these advanced features. It ensures backward compatibility while moving Ethereum forward.
```

```
Despite these enhancements, there’s still a major UX piece that needs to be unlocked in order to fully achieve a web2-like experience. That is, fine-grained and flexible permissions where the user can easily share the power to take a specific action on their behalf with another user. This is done in a controlled way, with strict guardrails and the ability to revoke permissions when needed.
```

```
This is the challenge the Delegation Toolkit is built to solve. But before diving into what it is and how it works, let’s take a closer look at the two ERCs driving this next phase of Ethereum account UX.
```

## ERC 7710: Smart contract delegation

```
ERC-7710 is a proposal to standardize how smart contract accounts delegate specific permissions to other Ethereum accounts. It introduces a common interface for delegators to grant authority to delegates with clear, enforceable rules.
```

```
With ERC-7710, developers can build systems where one account safely allows another to act on its behalf under certain conditions, like spending limits, time restrictions, or task-specific permissions. All of this is handled through a secure and predictable delegation flow.
```

```
So, what does “delegation” mean in this context?
```

```
At a high level, it’s about separating account ownership from account execution. A delegator still owns the assets or logic but can hand off specific powers to someone else temporarily or permanently without giving up full control.
```

```
From a network perspective, ERC-7710 brings clarity and composability to how permissions are passed between accounts. Instead of every app rolling out its own delegation logic, we now have a shared foundation that contracts and tools can build on.
```

## ERC 7715:  Grant permissions from wallets

```
ERC-7715 is a proposal to streamline how dapps request permissions from user wallets. It introduces a permissioned model that allows dapps to ask for clearly scoped, pre-approved access to a user’s account, without needing repeated confirmations for every interaction.
```

```
Today, the typical interaction flow is clunky: a dapp asks to send a transaction or interact with a contract, and the wallet prompts the user for approval every single time. There's no way to specify limits, durations or narrow the scope of what a dapp can do, and no standard way for users to view or revoke these approvals later.
```

```
ERC-7715 changes that. It introduces a new method, wallet_grantPermissions, which lets a dapp ask for specific capabilities in advance. For example, it might request permission to spend up to 10 USDC over the next hour or interact with a single smart contract for the next few sessions. The user reviews this upfront and approves or rejects it with full clarity.
```

```
From a dapp’s perspective, this makes onboarding smoother and allows for session-based or automated experiences that don’t constantly interrupt the user. For wallets, it introduces a clean, auditable structure for handling permissions and managing what’s been granted. For users, it offers better visibility, more control, and fewer annoying pop-ups, all without sacrificing security.
```

### So, what can developers build with these standards?

```
Here are some features that can be unlocked by combining ERC-7710 and ERC-7715:
```

- ```
Set daily spending limits from a secure wallet by using ERC-7710 to delegate a fixed allowance to a more frequently used wallet, keeping most funds safely offline.
```

- ```
Simplify onboarding by enabling users to perform actions like minting or claiming rewards without prior wallet setup or transaction approvals, using pre-scoped delegations that grant limited permissions automatically.
```

- ```
Let AI agents trade on your behalf by assigning limited execution permissions via ERC-7710, with ERC-7715 making it easy to approve those permissions through your wallet.
```

- ```
Let a dapp cover users’ gas fees by assigning sponsorship rights to an SCA leveraging ERC-7710 delegation, allowing specific transactions to go through without the user needing ETH.
```

- ```
Revoke token approvals safely by delegating permission to a helper contract that calls approve(0) on your behalf, using ERC-7710 to tightly scope what it can do and ensure compatibility with any standard ERC-20 token.
```


```
And lots more.
```

## Defining the Delegation Toolkit

```
The easiest way to describe the Delegation Toolkit is as a suite of tools that includes two main packages:
```

1. ```
The Delegation Core SDK – A package that enables developers to create ERC-4337-compliant smart contract accounts (delegator accounts) that benefit from all the features introduced by ERC-4337 account abstraction.
```

2. ```
The Delegation Framework – A system that allows smart contract accounts to delegate permissions to other Ethereum accounts through the ERC-7710 standard and allows websites, dapps, or eventually anything to request permissions from your account through ERC-7715 (coming soon).
```


```
So, in simple terms, the Delegation Toolkit makes it possible to build dapps that support ERC-4337 smart contract accounts (SCAs), delegate permissions to other SCAs or EOAs, and enable dapps to request those permissions, whether from smart accounts or, following the Pectra upgrade, even EOAs.
```

```
If you’re familiar with how smart contract accounts work, you already know what to expect from the Delegation Core VIEM package. But if not, feel free to check out the official docs for a deeper dive. In the rest of this article, we’ll zoom in on the Delegation Framework itself and highlight some of the key principles that make it so powerful.
```

### Off-chain delegation

```
Delegations don’t need to live on-chain to be valid. Once Account A signs a delegation for Account B, it can be stored anywhere—IPFS, browser cache, cloud storage, or even downloaded locally, because it’s cryptographically secure and only usable by the intended delegate. This makes the whole system more scalable and flexible.
```

```
For example, B can fetch the signed delegation when needed and use it in a transaction without requiring any on-chain storage or upfront gas costs.
```

### Revokable permissions

```
Delegations are not forever. The original delegator, say Account A, can revoke any active delegation it has issued, even if that delegation has already been passed down through transitive delegation (see section on Transitive or chained delegation below). Once revoked, the delegation manager will reject any attempts to redeem it, including any sub-delegations that depend on it. This is critical for safety.
```

```
If Account A suspects compromise or wants to update permissions, it can instantly cut off access, rendering the entire chain of trust invalid. It’s like being able to pull the plug at any time without needing to track down everyone involved.
```

### Scoped delegations

```
With the Delegation Toolkit, delegations are typically scoped to a specific delegate. That means if Account A signs a delegation to Account B, only B can redeem it. Even if someone else gets hold of the signed delegation, it’s useless to them as it’s cryptographically bound to B.
```

```
However, there’s also support for open delegations. These are delegations that don’t specify a delegate upfront and can be redeemed by any account. Open delegations are useful when you don’t yet know who the delegate will be, but they should be used with caution to avoid misuse. You can create one by setting the delegate field to the special constant ANY_BENEFICIARY (0x0000000000000000000000000000000000000a11).
```

```
If Account B later wants to pass on a scoped permission to Account C, it must explicitly create a new delegation from B to C. This ensures that permission boundaries remain clear and intentional, even when using open or transitive flows.
```

### Caveats

```
Caveats are the rules that define how a delegation can be used. Think of them as custom conditions the delegator can attach to a delegation. For example, if Account A delegates to Account B, it might include a caveat saying B can only transfer up to 1 ETH or only interact with a specific contract within the next 24 hours.
```

```
Caveats are enforced at runtime using special caveat enforcer contracts, which check that the rules are followed before any action is executed. So even if B has the right to act, they’re still operating within a well-defined box set by A. See here for the full list of supported caveats or learn how to create your own custom caveat.
```

### Transitive or chained delegation

```
One of the more powerful features of the Delegation Framework is that delegation doesn’t have to stop with a single handoff. With transitive delegation, an account that receives permission, say Account B, can turn around and delegate all or part of that authority to another account, forming a chain of delegations originating from one account.
```

```
Each link in the chain is cryptographically signed and scoped with its own caveats, meaning permissions can be narrowed or redefined at every level. In other words, these sub-delegations can be a subset of the original, with stricter constraints or limited capabilities.
```

```
Importantly, the original delegator still maintains full control and can revoke the initial delegation at any time, which automatically invalidates all downstream delegations in the chain, creating a flexible yet secure chain of trust, ideal for complex permission setups like DAOs and shared wallets.
```
