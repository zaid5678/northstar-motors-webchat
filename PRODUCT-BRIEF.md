# Northstar Motors AI Webchat

## Product goal

Build an AI webchat for the Northstar Motors website. It should help customers find
relevant vehicles, get accurate dealership information, and complete common sales and service
tasks in a natural conversation.

The dealership platform provides access to the existing inventory, sales, workshop, and contact
systems.

The assistant should provide a helpful and natural conversational experience for Northstar Motors
customers.

## Core experience

The webchat should:

- be available throughout the website without obscuring important page content;
- work well on desktop and mobile screen sizes;
- make its open, closed, loading, unread, unavailable, and error states clear;
- support normal multi-turn conversation, including sending, scrolling, and reviewing prior
  messages;
- preserve a customer's conversation when the page is refreshed;
- understand relevant context from the page where the conversation begins;
- show progress while a response or business operation is in flight;
- make generated links, vehicle results, choices, and confirmations easy to use;
- be usable with a keyboard and assistive technology;
- recover gracefully from failed or interrupted requests.

## Customer capabilities

### Vehicle discovery

Customers should be able to:

- search stock using natural descriptions, preferences, and constraints;
- refine or change requirements during the conversation;
- compare relevant vehicles;
- view the important details needed to decide what to do next;
- follow a result to the corresponding vehicle on the website;
- understand whether a vehicle is available, reserved, or sold;
- discover published new-car offers without inventing prices or terms.

### Sales support

Customers should be able to:

- ask whether a vehicle is available;
- make a general, availability, finance, or part-exchange sales enquiry;
- find suitable test-drive availability and make a confirmed booking;
- register interest when a vehicle is currently reserved;
- request a sales callback with useful timing and context;

### Workshop support

Customers should be able to:

- discover supported service types and suitable workshop locations;
- make a new workshop booking;
- amend an existing workshop booking;
- cancel an existing workshop booking;
- retrieve the details and status of an existing workshop booking after verifying the customer's
  identity.

### Contact and dealership information

Customers should be able to:

- find dealership addresses, contact details, departments, and opening hours;
- understand holiday opening-hour exceptions;
- leave a message for an appropriate dealership department;
- request a callback;
- obtain an indicative part-exchange estimate with the applicable qualification;
- receive accurate finance, privacy, and part-exchange notices when relevant.

## Technical scope

Implement the webchat as a complete application, including its customer interface, server-side
logic, persistent conversation state, LLM configuration, dealership-platform integration, error
handling, and operational logging.

The product should handle customer information and dealership operations securely and
responsibly.

Do not add a pre-built webchat product or an existing webchat implementation. General-purpose
frameworks and libraries may be used.

The webchat may use an LLM provider but must not require any other hosted service.

## Running the product

The product must run from a clean checkout. The repository must include:

- concise setup and start instructions;
- the required environment-variable names without committing secret values;
- any database setup or migration steps;
- a short description of important product decisions and known limitations;
- automated checks for the most important behaviour.

The webchat and dealership services must run concurrently on their documented ports.
Do not change the dealership platform's behaviour or seeded data.

## Local dealership services

Run the dealership services from this directory:

```bash
docker compose up --build -d
```

- Vehicle website: http://localhost:4173
- Dealership Platform API: http://localhost:4010
- API documentation: http://localhost:4010/docs
- Dealership Systems Console: http://localhost:4010/admin

The website is editable and is the host site for the webchat. The Systems Console shows records
received by the dealership platform and recent API activity.

Read [docs/INTEGRATION-GUIDE.md](./docs/INTEGRATION-GUIDE.md) before integrating with the local
platform.
