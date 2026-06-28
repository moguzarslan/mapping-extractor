class Prompts:
    REQUIREMENT_EXTRACTION_PROMPT = """
#  Objective
    You are an expert software requirements analyst. Extract functional requirements, quality requirements, constraints and acceptance criteria from the provided software document.
    
# Instructions
    1. Review the entire document.
    2. Identify all functional requirements (FR), quality requirements (QR), and constraints and their acceptance criteria for all of them.
    3. For each identified FR, QR, constraint and criterion apply the specific processing logic found in the Extraction Process by Type section below, during the extraction make sure to oblige the rules defined in Rules section.

# Extraction Process by Type

## For All Types
   	- Assign a unique, strict sequential ID starting from R_01 (e.g., R_01, R_02, R_03...) regardless of type.
    - For each type, specify the page number from the document text (not the PDF page number) where the requirement starts.
    - For each type, specify the type (FR, QR, constraint or criterion).
         
## For FR
    - In the case of a user story AS A actor I WANT something IN ORDER TO whatever, extract I WANT something.
    - In the case of a functional requirement expressed in a use case specification, extract the objective.
    - In the case of a FR expressed as a plain text without a specific format, FR is the text.
	- Extracted FR should have the following JSON Schema:
		{
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type": "<FR>",
        "description": "<FR in English>",
		“pageNumber”: “<Page number of the FR>”
        }

## For QR
    - For QRs, concept and categorization should be extracted from the document.
        - In case of a QR expressed using Volere, the concept is the QR type written in the Requirement part and categorization is Volere.
        - In case of a QR classified from ISO/IEC 25010, the concept is the subcharacteristic or, in case it is not stated, the characteristic. And the categorization is ISO/IEC 25010.
    - In case of a QR expressed using Volere with Description and Acceptance, extract the Description contents.
    - In the case of a QR expressed as a plain text without a specific format, FR is the text.
	- Extracted QR should have the following JSON schema:
		{
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type": "<QR>",
        "description": "<QR in English>",
		“pageNumber”: “<Page number of the requirement>”,
        "concept": "<Type of QR (in English) >",
        "categorization": "<Volere or ISO/IEC 25010>"
        }

## For Constraint
	- Extracted constraint should have the following JSON Schema:
        {
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type": "<Constraint>",
        "description": "<Constraint in English>",
		“pageNumber”: “<Page number of the Constraint>”
        }	
        	
## For Criterion
    - In the case of a user story with several acceptance criteria, they should be added as separate criteria.
    - In case of a QR expressed using Volere with Description and Acceptance, extract the Acceptance contents.
    - The link between an acceptance criterion and its related requirement should be explicitly represented by stating the ID of the related requirement.
    - Extracted criterion should have the following JSON schema:
		{
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type":”<criterion>”
		"description": “<Actual acceptance criterion of the requirement in English>,>”,
		"pageNumber": “<Page number of the criterion>”,
        "relatedTo":”<Id of the related requirement>”
        }
        
# Rules:
- Ensure that all information is strictly supported by the document.
- Output should be given in JSON format as in Example Output section.
- Whole output must be English. If the source document is in another language, translation should be made while extracting to ensure all extracted fields are in English.
- Avoid extracting:
    - Implementation workflows and processing sequences.
    - Database queries, algorithms, or internal logic.
    - Database constraints even though they are under "constraints" or "integrity constraints".
    - API routes, method names, class names, or source code details.
    - Data model definitions (tables, entities, foreign keys, schemas).
    - Development tools, IDEs or build systems.
    - Deployment and infrastructure details unless explicitly stated as a stakeholder requirement
    Some invalid requirement examples:
    "After receiving the input, the information is passed to another internal component for processing."
    "The system executes a database query to retrieve the requested data."
    "The functionality is exposed through a specific API endpoint and HTTP method."
    "The application stores its data using a particular database technology."
    "The database schema contains tables, fields, primary keys, foreign keys, and relationships between entities."
    "The software is implemented using a specific programming language, framework, or cloud platform."
- Avoid extracting criterion from post conditions or procedures fields defined in the document.

 
# Example Output (JSON)
 {
  "id": "R_01",
  "type": "FR",
  "description": "The system shall allow users to reset their password via email verification.",
  "pageNumber": "12"
},
{
  "id": "R_02",
  "type": "criterion",
  "description": "When a registered user requests a password reset, the system shall send a password reset email containing a valid verification link within 1 minute.",
  "pageNumber": "12",
  "relatedTo": "R_01"
},
{
  "id": "R_03",
  "type": "QR",
  "description": "The system shall respond to user requests within 2 seconds under normal operating conditions.",
  "pageNumber": "15",
  "concept": "12a. Speed and latency",
  "categorization": "Volere"
},
{
  "id": "R_04",
  "type": "criterion",
  "description": "During performance testing with up to 1,000 concurrent users, 95% of requests shall complete within 2 seconds.",
  "pageNumber": "15",
  "relatedTo": "R_03"
}
    ... 
    """

    REQUIREMENT_SPLITTING_PROMPT = """
    # Objective:
    You are an expert software requirements analyst. You are given a JSON of already extracted requirements. Your task is to review each requirement/criterion and split it into multiple atomic requirements/criteria ONLY when it clearly expresses more than one independent need.

    # Instructions:
    1. Process each requirement/criterion in the input JSON one by one.
    2. Decide whether the requirement expresses a single need (atomic) or multiple needs (compound). Obligate the rules defined by the Rules section.
       - If atomic, keep it unchanged (same id and same field values).
       - If compound, split it into the minimum number of atomic requirements, one per distinct need:
         - Preserve the original sentence structure for each split part so each resulting requirement reads as a complete, standalone statement (repeat the shared subject/predicate as needed).
         - Keep the original id as the base and append sequential lowercase letters (R_01 -> R_01a, R_01b, R_01c ...).
    3. Handle the "relatedTo" field (a criterion's link to the requirement it verifies, given as that requirement's id):
       - Always carry "relatedTo" through to the output.
       - Splitting a requirement changes its id (R_01 -> R_01a, R_01b, ...). When a target is split, re-point every "relatedTo" that referenced the original id to the split part whose description best matches it.
       - Never leave a "relatedTo" referencing an id that no longer exists.

    # Example Split:
    - {
        "id": "R_01",
        "description": "The system should support English and Turkish."
      }
      ->
      {
        "id": "R_01a",
        "description": "The system should support English."
      },
      {
        "id": "R_01b",
        "description": "The system should support Turkish."
      }

    # Example relatedTo realignment (a referenced requirement is split):
    - {
        "id": "R_05",
        "description": "The system shall authenticate users and register new accounts."
      },
      {
        "id": "R_06",
        "description": "Authentication must lock the account after three failed attempts.",
        "relatedTo": "R_05"
      }
      ->
      {
        "id": "R_05a",
        "description": "The system shall authenticate users."
      },
      {
        "id": "R_05b",
        "description": "The system shall register new accounts."
      },
      {
        "id": "R_06",
        "description": "Authentication must lock the account after three failed attempts.",
        "relatedTo": "R_05a"
      }

    # Rules:
    - Preserve the original order of requirements.
    - Avoid splitting when the conjunction joins parts of a single indivisible need (e.g. "username and password" forming one credential, "save and exit" as one action if treated atomically in the source).
    - Avoid splitting closely related, paired, or opposite actions on the same target (e.g. "create/update/delete", "add or remove", "enable or disable", "assign or revoke", "grant or deny").
    - Avoid splitting an enumeration that defines the allowed values, permitted states, options, or range of a single attribute (e.g. "The order status can only be open or closed."); the "and"/"or" lists a value domain, not separate needs. 
    - Avoid splitting an illustrative or parenthetical list of examples (e.g. "working with multiple technologies (android, ios)" is one compatibility requirement).
    - Avoid splitting a list of attributes, properties, fields, or aspects of a SINGLE subject that share one common predicate or quality (e.g. "An entity should have a date, name, id and address.", "The layout, spacing, and typography must match the style guide."). Split a list ONLY when each listed item is a distinct resource governed by its own rule, permission, or behaviour (e.g. the orders/invoices/shipments example above). Do NOT split when a single read-only operation (view, list, display, show) presents several objects together (e.g. "The user can view their profile and products." is one viewing capability).
    - Avoid splitting coordinated adjectives or qualifiers that describe a single property or need (e.g. "continuous and uninterrupted service").
    - Avoid splitting conditions, parameters, thresholds, or metrics that together qualify one single test, measurement, or activity (e.g. "Load testing with at least 100 concurrent users achieving a response time under 2 seconds.").
    - Avoid changing the meaning of any requirement.
    - Avoid adding any explanation or commentary.

    # Input Format (JSON):
    {
      "requirements": {requirements_json}
    }

    # Example Output (JSON):
    {
      "requirements": [
        {
          "id": "R_01a",
          "description": "The system shall send notifications by email."
        },
        {
          "id": "R_01b",
          "description": "The system shall send notifications by SMS."
        },
        {
          "id": "R_02",
          "description": "...",
          "relatedTo": "R_01a"
        }
      ]
    }
    ...
    """

    CRITERION_CLEANUP_PROMPT = """
    # Objective:
    You are an expert software requirements analyst. You are given a JSON of already extracted acceptance criteria. Each criterion has an id, a description, and "relatedRequirement" — the description of the requirement it belongs to, given as read-only context for comparison. Your task is to review each acceptance criterion and remove the ones that do not add value.

    # Instructions:
    1. Process each acceptance criterion in the input JSON one by one. Obligate the rules defined by the Rules section.
       - If the criterion must be removed, omit it from the output entirely.
       - Otherwise, keep it unchanged (same id and same description).
    2. Use "relatedRequirement" only as context to judge the criterion; never output it and never treat it as a criterion of its own.
    3. Preserve the original order of the remaining criteria.

    # Rules:
    - Default to KEEPING every criterion. Removal is the exception, not the norm; when unsure, keep it.
    - Decide with this test: a criterion is redundant ONLY if successfully verifying its related requirement would, on its own, already verify the criterion — i.e. they amount to the same single check with nothing extra to confirm. If verifying the requirement would NOT automatically confirm the criterion, the criterion adds value and must be kept.
    - Under that test, keep the criterion whenever it pins down anything the requirement leaves open, even with similar wording — for instance a measurable threshold or metric, a required verification/monitoring/testing method or acceptance evidence, a narrower scope, or an extra qualifying condition.
    - Separately, remove a criterion that states only a post condition or a procedure / sequence of steps rather than an acceptance condition (e.g. "After entering the credentials, the user must access the main view.").
    - Reworded phrasing alone is never a reason to remove a criterion.
    - Avoid changing the meaning, wording, or id of the criteria that are kept.
    - Avoid adding any explanation or commentary.

    # Input Format (JSON):
    {
      "requirements": {requirements_json}
    }

    # Example Output (JSON):
    {
      "requirements": [
        {
          "id": "R_02",
          "description": "When a registered user requests a password reset, the system shall send a password reset email containing a valid verification link within 1 minute."
        },
        {
          "id": "R_04",
          "description": "During performance testing with up to 1,000 concurrent users, 95% of requests shall complete within 2 seconds."
        }
      ]
    }
    ...
    """

    ARCHITECTURAL_UNIT_EXTRACTION_PROMPT = """
# Objective
    You are an expert software architect and system design analyst. Extract the Architectural Units (the concrete building blocks of the system) from the provided software document.

# Instructions
    1. Carefully read the entire document.
    2. Identify all Architectural Units across the entire document following the definitions and rules below.

# Type Definitions
Below are the definitions for each type and their concrete examples.

## Layer
    - Definition: a horizontal tier that groups units by a shared responsibility in a layered / n-tier architecture.
    - Examples: presentation layer, business / business logic layer, domain layer, data-access layer, contract layer, service layer, persistence / data layer, infrastructure layer.

## Component
    - Definition: a concrete structural module of THIS system that is not exposed as an independently running service.
    - Examples: frontend, backend, database, View, ViewModel, Model, controller, repository, cache, message broker.
    - NOT a Component: a specific named third-party product (that is a Technology); a module that runs independently or is an external integration (that is a Service).

## Service
    - Definition: a logical capability or independently running module of the system, including each microservice and each external / third-party service the system depends on, integrates with, or calls.
    - Examples: an API gateway, a microservice, an authentication / authorization / payment / email / analytics / logging / monitoring service.
    - NOT a Service: the specific named product or vendor that implements the capability (that is a Technology); a horizontal tier (Layer).

## Device
    - Definition: a physical hardware endpoint or piece of equipment that participates in the system.
    - Examples: a sensor, a screen / display, a kiosk or player device, a mobile or desktop device, an IoT device, a hardware appliance.
    - NOT a Device: a network or medium such as the Internet; a software process (Service / Other).

## Technology
    - Definition: a specific, named product, framework, library, programming language, protocol, cloud service, or development / testing / monitoring tool used to build or run the system, including a named third-party product that provides an external service or integration.
    - Examples: 
        - a communication protocol (e.g. HTTP, HTTPS, REST, TCP, WebSocket, gRPC etc.)
        - a programming language or framework (Java, Python, Next.js etc.)
        - a database engine (PostgreSQL, MySQL, Oracle etc.)
        - a cloud service (AWS, Azure, etc.) 
        - a monitoring / analysis / testing tool (Grafana, Dynatrace, Sonarqube etc.) 
    - NOT a Technology: the generic capability the product provides for this system (that is a Service).

## Other
    - Definition: an external actor that directly interacts with the running system as part of its architecture, and does not fit any technical type above.
    - Examples: an end user, client.
    - NOT Other: a physical hardware device (Device); a software service of the system (Service).

# Extraction Process
    - Assign each Architectural Unit a strict sequential id: AU_01, AU_02, AU_03...
    - For each unit, specify its type using exactly one of: "Layer", "Component", "Service", "Device", "Technology" or "Other".
    - For each unit "description" must be the sentence or sentences from the document that states the unit — the evidence proving the unit.
    - For each unit, specify the page number from the document text (not the PDF page number) where it is described. If it spans several pages, list them all.
    - For each unit, "isPartOf" is the list of OTHER Architectural Unit ids (AU_xx) this unit is contained by or belongs to. Build the containment hierarchy among units explicitly:
        - A Technology isPartOf the Service / Component that uses it.
        - A Service / Component isPartOf its Layer; if it has no Layer, isPartOf its parent Service / Component.
        - Leave "isPartOf" as an empty list when no containing unit applies.
    - If the source document misclassifies a unit (e.g. a Service labelled as a Component), correct the classification and document your reasoning in the "fixes" field. Otherwise, leave "fixes" empty.
    - Architectural Unit should have the following JSON Schema:
        {
        "id": "<Sequential Architectural Unit id (AU_01, AU_02)>",
        "type": "<Layer | Component | Service | Device | Technology | Other>",
        "name": "<Name of the unit in English >",
        "description": "<The exact document sentence(s) stating the unit, translated to English>",
        "pageNumber": "<Page number(s) where the unit is described>",
        "isPartOf": ["<id of the unit this unit is part of>"],
        "fixes": ["<Brief explanation if any mistake was corrected from the source document>"]
        }

# Rules:
    - Ensure every unit is strictly supported by the document; do not output a unit or technology whose name or role does not actually appear in the source, and include an inferred unit only when the evidence is strong.
    - Extract units from every view and section of the document (e.g. deployment, frontend structure, backend layering), not only from a single section.
    - Extract each distinct unit individually, including units that are only listed together, named in passing, or mentioned in prose; never collapse several distinct units into one.
    - Extract each real unit exactly once: do not output the same unit twice under different names, and do not split one real unit into several.
    - When a structural or presentation pattern (e.g. MVC / MVVM) names its constituent parts, extract each named part as its own Component (View, ViewModel, Controller, View, etc.).
    - Name each service by its capability or function (e.g. authentication, payment, storage), not by the product that implements it; when a named product provides the capability, additionally output that product as a separate Technology whose isPartOf is the Service.
        - Apply this especially to external / third-party integrations named by their product (e.g. a payment, email, storage, authentication, analytics, or monitoring provider). Output BOTH units, never the product alone:
            - a Service named by the capability it provides
            - a Technology named by the product, whose isPartOf is that Service.
    - Extract every named technology, including ones mentioned only in the prose.
    - Extract every named communication protocol (e.g. HTTP, HTTPS, REST, TCP, WebSocket, gRPC) as its own Technology, including protocols named only in passing or in the prose; never omit a protocol as a mere implementation detail.
    - Output should be given in JSON format as in the Example Output section.
    - The whole output must be English. If the source document is in another language, translation should be made while extracting to ensure all extracted fields are in English.
        - During translating, avoid to paraphrase, summarize, reword, or normalize phrasing.
    - Avoid adding any extra explanation, just provide the required data.

# Example Output (JSON)
{
  "architectural_units": [
    {
      "id": "AU_01",
      "type": "Other",
      "name": "Customer",
      "description": "The client is the user of the platform.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_02",
      "type": "Other",
      "name": "Server",
      "description": "The server is the software that is responsible for processing user requests.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_03",
      "type": "Layer",
      "name": "Presentation Layer",
      "description": "It is the layer that is responsible for displaying information to the user.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_04",
      "type": "Layer",
      "name": "Business Layer",
      "description": "This layer is the core of the application and is responsible for processing all the information.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_05",
      "type": "Layer",
      "name": "Data Layer",
      "description": "It is the layer that is responsible for storing all the data.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_06",
      "type": "Service",
      "name": "API Gateway Service",
      "description": "This service is responsible for the management, authentication and authorization of platform users.",
      "pageNumber": "44",
      "isPartOf": ["AU_04"],
      "fixes": []
    },
    {
      "id": "AU_07",
      "type": "Technology",
      "name": "Spring Boot",
      "description": "Backend framework used to implement the API Gateway Service.",
      "pageNumber": "44",
      "isPartOf": ["AU_06"],
      "fixes": []
    }
  ]
}
    ...
    """

    PATTERN_EXTRACTION_PROMPT = """
# Objective
    You are an expert software architect and system design analyst. Extract the Patterns (the reusable design solutions the system is built upon) from the provided software document. Do NOT extract Architectural Units (layers, components, services, technologies, devices, connectors) — those are produced by a separate prompt.

# Instructions
    1. Carefully read the entire document.
    2. Identify all architectural and design patterns that are explicitly stated in the document.

# Pattern Definitions
    Below are the definitions for each type and their concrete examples.

## Architectural Pattern
    A high-level structural organization of the system (e.g. Client-Server, Layered Architecture, Microservices, MVVM, Service-oriented, Cloud Architecture).
## Design Pattern 
    A lower-level software design solution used within units (e.g. API Gateway, Repository, Observer, Singleton, Shared Database, ORM, Component-based).

# Extraction Process
    - Assign each Pattern a strict sequential id: P_01, P_02, P_03...
    - For each pattern, specify the page number from the document text (not the PDF page number) where it is described. If it spans several pages, list them all.
    - For each pattern "description" must be the sentence or sentences from the document that states the pattern — the evidence proving the pattern.
    - For each pattern, specify its type using exactly one of:
    - "isPartOf" is the list of OTHER Pattern ids (P_xx) this pattern belongs to (e.g. "Three Layers" isPartOf "Client-Server"; a Design Pattern isPartOf the Architectural Pattern that introduces it). Leave it as an empty list when no parent pattern applies. Do NOT reference architectural units here — relationships between patterns and units are out of scope for this step.
    - If the source document misclassifies a pattern (e.g. a Design Pattern labelled as an Architectural Pattern), correct the classification and document your reasoning in the "fixes" field. Otherwise, leave "fixes" empty.
    - Pattern should have the following JSON Schema:

        {
        "id": "<Sequential Pattern id (P_01, P_02)>",
        "type": "<Architectural Pattern | Design Pattern>",
        "name": "<Name of the pattern in English>",
        "description": "<The exact document sentence(s) stating the pattern, translated to English>",
        "pageNumber": "<Page number(s) where the pattern is described>",
        "isPartOf": ["<id of the pattern this pattern is part of>"],
        "fixes": ["<Brief explanation if any mistake was corrected from the source document>"]
        }

# Rules:
    - Ensure that all information is strictly supported by the document. Do not output a pattern whose name does not actually appear in the source.
    - Output should be given in JSON format as in the Example Output section.
    - The whole output must be English. If the source document is in another language, translation should be made while extracting to ensure all extracted fields are in English.
        - During translating, avoid to paraphrase, summarize, reword, or normalize phrasing.

# Example Output (JSON)
{
  "patterns": [
    {
      "id": "P_01",
      "type": "Architectural Pattern",
      "name": "Client-Server",
      "description": "The platform is a web service that follows the client-server architecture, where the client makes requests to a server that processes them and returns the response.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "P_02",
      "type": "Architectural Pattern",
      "name": "Three Layers",
      "description": "The client-server model uses a 3-layer architecture, where the system is divided into 3 layers.",
      "pageNumber": "41",
      "isPartOf": ["P_01"],
      "fixes": []
    },
    {
      "id": "P_03",
      "type": "Architectural Pattern",
      "name": "Service-oriented",
      "description": "Both the business layer and the data layer are divided into several different services.",
      "pageNumber": "42",
      "isPartOf": ["P_01"],
      "fixes": []
    },
    {
      "id": "P_04",
      "type": "Design Pattern",
      "name": "API Gateway",
      "description": "This design pattern allows only one component to interact between users and the services provided by the platform.",
      "pageNumber": "43",
      "isPartOf": ["P_03"],
      "fixes": []
    }
  ]
}
    ...
    """

    CONNECTOR_EXTRACTION_PROMPT = """
# Objective
    You are an expert software architect and system design analyst. Given the already-extracted Architectural Units (provided below as JSON) and the software document, extract the Connectors of the system — the communications between its Architectural Units.

# Instructions
    1. Carefully read the entire document.
    2. Use the provided Architectural Units as the endpoints, referring to them by their given ids (AU_xx).
    3. Sweep the document text section by section and extract one Connector for every communication stated between units.
    4. A single Connector may link two units (one-to-one) or one unit to several units (one-to-many).

## What is connector
    - Definition: a communication or interaction relationship among two or more Architectural Units. It can link any unit type to any unit type (e.g. layer–layer, service–service, service–database, device–service, component–service ...). A Connector has no name.
    - One-to-one: a communication stated between two units.
    - One-to-many: a single stated communication in which one unit communicates with several other units (e.g. a unit that routes, distributes, logs, or mediates communication among many units).
    - NOT a Connector: the protocol or technology used for the communication (that is a Technology); any of the units at the ends of the communication; a hosting, deployment, containment or management relationship (e.g. one unit hosts, runs, deploys, contains or manages another) — that is structure, not communication.
    - NOT a Connector: an individual step of a use-case, scenario, user flow, or action sequence that narrates runtime behaviour over time (e.g. a user performing an action and the system responding step by step) — this is behaviour, not a structural communication between architectural units.

# Extraction Rules
    - Continue the units' AU_xx id sequence: give each Connector the next sequential AU id after the highest AU id among the provided units (e.g. if the units end at AU_20, the connectors are AU_21, AU_22, AU_23...). Never reuse an id that already belongs to a provided unit.
    - "isPartOf" is the list of the TWO OR MORE Architectural Unit ids (AU_xx, taken from the provided units) that the Connector links. Include exactly the units the stated communication involves. For a one-to-many communication, list the central (hub) unit FIRST, followed by every unit it communicates with.
    - "description" must be the sentence or sentences from the document that states the communication — the evidence proving the connection.
        - In case of multiple sentences from different pages, sentences should be separated with "[...]"
    - Leave "name" empty.
    - Specify the page number from the document text (not the PDF page number) where the communication is described. If it spans several pages, list them all.
    - Connector should have the following JSON Schema:
        {
        "id": "<Next sequential AU id continuing from the provided units (e.g. AU_21, AU_22)>",
        "type": "Connector",
        "name": "",
        "description": "<The exact document sentence(s) stating the communication, translated to English>",
        "pageNumber": "<Page number(s) where the communication is described>",
        "isPartOf": ["<id of a linked unit>", "<id of another linked unit>", "..."],
        "fixes": []
        }

# Rules:
- A Connector links two or more units; put every unit the stated communication involves in "isPartOf".
- When a single statement describes one unit communicating with a set of units in the same way (one-to-many — e.g. a unit that routes, distributes, logs, or mediates among many units), output ONE Connector with that central unit listed FIRST, followed by every unit in the set; do not split it into separate pairs.
- Output a separate Connector for each independently stated communication; do not merge communications the document states separately into one Connector.
- Do not extract the individual steps of a use-case, scenario, or action sequence as connectors; capture only the structural communications between the architectural units involved.
- A Connector may link any unit type to any unit type.
- Extract connectors at every level of abstraction the document describes — between layers, between a layer and a service or component, and between services. When the document states a communication between two layers or tiers, connect those layer units directly; never substitute the services or components inside a layer for the layer itself.
- When one statement describes a communication at several levels (e.g. a layer communicating with another layer, and more specifically with a service inside it), output a separate Connector for EACH stated level; do not collapse them into one.
- Reference only ids from the provided Architectural Units; if an endpoint you see is not in the list, link it to the closest matching provided unit.
- Extract communications from every view and section described in the prose, not only from a single section.
- Ensure every connector is strictly supported by the document; do not invent communications. Do not infer a connector merely because two units could plausibly interact (for example, do not connect every service to a shared database or infrastructure unit) — extract only the communications the document explicitly states.
- Output should be given in JSON format as in the Example Output section.
- The whole output must be English. If the source document is in another language, translation should be made while extracting to ensure all extracted fields are in English.
    - During translating, avoid to paraphrase, summarize, reword, or normalize phrasing.

# Example Output (JSON)
{
  "connectors": [
    {
      "id": "AU_07",
      "type": "Connector",
      "name": "",
      "description": "The presentation layer communicates with the business layer. [...] presentation layer communicates with business layer for calling the necessary functions.",
      "pageNumber": "42,61",
      "isPartOf": ["AU_03", "AU_04"],
      "fixes": []
    },
    {
      "id": "AU_08",
      "type": "Connector",
      "name": "",
      "description": "The gateway service routes every incoming request to the corresponding microservice.",
      "pageNumber": "44",
      "isPartOf": ["AU_06", "AU_09", "AU_10", "AU_11"],
      "fixes": []
    }
  ]
}
    ...
    """

    ISPARTOF_LINKING_PROMPT = """
# Objective
    You are an expert software architect and system design analyst. You are given the already-extracted Architectural Units and Patterns of a system (as JSON, with id/type/name/description) and the software document. Determine the "isPartOf" containment relationship for each provided element, referring to elements only by their given ids (AU_xx for units, P_xx for patterns).

# Instructions
    1. Carefully read the entire document.
    2. For each provided Architectural Unit and Pattern, decide which element it is contained by or belongs to, following the containment rules below.
    3. Refer to every element by its given id; the result must reference only ids present in the provided input.

# Containment Rules
    - A Technology isPartOf the Service or Component that uses it.
    - A Layer, Service, or Component that constitutes or realizes a named Pattern isPartOf that Pattern (P_xx).
    - A Layer, Service, or Component that does not constitute a Pattern isPartOf its parent unit (its Layer, or its parent Service / Component).
    - A Pattern isPartOf the parent Pattern it belongs to.
    - Assign each element a single primary container; list more than one id only when the document clearly states the element belongs to several.
    - Leave "isPartOf" as an empty list when no containing element applies.

# Rules:
    - Reference only ids from the provided input (AU_xx for units, P_xx for patterns); never invent an id.
    - Base every relationship strictly on the document; do not infer a containment the document does not support.
    - Return one entry per provided element, giving only its id and its isPartOf; do not repeat other fields and do not add any extra explanation.
    - Output should be given in JSON format as in the Example Output section.

# Example Output (JSON)
{
  "isPartOf": [
    { "id": "AU_03", "isPartOf": ["P_02"] },
    { "id": "AU_07", "isPartOf": ["AU_06"] },
    { "id": "P_04", "isPartOf": ["P_01"] }
  ]
}
    ...
    """

    MAPPING_EXTRACTION_PROMPT = """
    Objective:

    You are an expert software architect and requirements engineer.
    Your task is to create a mapping between architectural components and requirements based on the provided software product document.

    Instructions:
    1. Carefully read the entire document.
    2. Carefully inspect provided images.
    3. First, identify and extract all functional, non-functional requirements and constraints.
    4. Then identify all architectural item mentioned explicitly or strongly implied by the document. Seperate the architectural items to:
        - Architectural Pattern: High-level structural organization of the system, such as microservices, layered architecture, client-server, event-driven architecture, hexagonal architecture, MVC, service-oriented architecture, etc.
        - Component: Concrete system building blocks or modules, such as frontend, backend service, API gateway, authentication service, database, message broker, cache, reporting module, etc.
        - Design Pattern: Lower-level software design solutions used within components, such as Strategy, Factory, Observer, Repository, Adapter, Singleton, Builder, etc. 
    5. Analyze how each item supports, implements, or is related to the identified requirements.
    6. For each architectural item, provide a detailed mapping that includes:
       - The relevant requirements it addresses (one or more).
       - A clear explanation of how the item fulfills or contributes to each requirement.
    7. The relationship between architectural items and requirements can be many-to-many.
    8. If the document is in a language other than English, translate all extracted information into clear English.
    9. Give page number reference for each section for each item. 

    
    Rules:
    - Ensure that all information is strictly supported by the document.
    - Give the corresponding mappings under three separate title (architectural patterns, components, design patterns) 
    - Give the output in json format.
    - Give the whole output in English.
    
    
    Output Format (JSON):
    {
      "mappings": {
        "architectural_patterns": [
          {
            "pattern_name": "<name>",
            "related_requirements": [
              {
                "requirement": "<Requirement>",
                "explanation": "<Why this requirement related to the architectural pattern>",
                "page_number": "<Page Number for the requirement>"
              }
            ],
            "page_number": [
              "<page numbers for the architectural pattern>"
            ]
          }
        ],
        "components": [
          {
            "component_name": "<name>",
            "related_requirements": [
              {
                "requirement": "<Requirement>",
                "explanation": "<Why this requirement related to the component>",
                "page_number": "<Page Number for the requirement>"
              }
            ],
            "page_number": [
              "<page numbers for the component>"
            ]
          }
        ],
        "design_patterns": [
          {
            "pattern_name": "<name>",
            "related_requirements": [
              {
                "requirement": "<Requirement>",
                "explanation": "<Why this requirement related to the design pattern>",
                "page_number": "<Page Number for the requirement>"
              }
            ],
            "page_number": [
              "<page numbers for the design pattern>"
            ]
          }
        ]
      }
    }
    ... 
    """

    CHAINED_MAPPING_EXTRACTION_PROMPT = """
    Objective:
    You are an expert software architect and requirements engineer.
    Your task is to create a mapping between architectural components and requirements using the provided JSON inputs.

    You will be given:
    1. A JSON containing extracted architectural items with their descriptions. Architectural items seperated into:
        - Architectural pattern
        - Component
        - Design Pattern
    2. A JSON containing extracted functional, non-functional requirements and constraints.

    Instructions:
    1. Carefully analyze both JSON inputs.
    2. For each architectural item, provide a detailed mapping that includes:
       - The relevant requirements it addresses (one or more).
       - A clear explanation of how the architectural item fulfills or contributes to each requirement.    
    4. Establish many-to-many relationships where applicable (a component can map to multiple requirements and vice versa).
    5. Base your mapping strictly on the provided data — avoid inventing unsupported relationships.
    6. Use architectural item explanations to justify mappings.
    
    Rules:
    - Give the corresponding mappings under three separate title (architectural patterns, components, design patterns) 
    - Give the output in json format.
    - Give the whole output in English.

    Input Format:
    {
      "requirements": {requirements_json}
      "architecture": {architecture_json},
    }
    
    Output Format (JSON):
    {
      "mappings": {
        "architectural_patterns": [
          {
            "pattern_name": "<name>",
            "related_requirements": [
              {
                "requirement": "<Requirement>",
                "explanation": "<Why this requirement related to the architectural pattern>"
              }
            ]
          }
        ],
        "components": [
          {
            "component_name": "<name>",
            "related_requirements": [
              {
                "requirement": "<Requirement>",
                "explanation": "<Why this requirement related to the component>"
              }
            ]
          }
        ],
        "design_patterns": [
          {
            "pattern_name": "<name>",
            "related_requirements": [
              {
                "requirement": "<Requirement>",
                "explanation": "<Why this requirement related to the design pattern>"
              }
            ]
          }
        ]
      }
    }
    ...
    """
