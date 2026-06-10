class Prompts:
    REQUIREMENT_EXTRACTION_PROMPT = """
#  Objective
    You are an expert software requirements analyst. Extract functional requirements, quality requirements, constraints and acceptance criteria from the provided software document.
    
# Instructions
    1. Review the entire document.
    2. Identify all functional requirements (FR), quality requirements (QR), and constraints and their acceptance criteria for all of them.
    3. For each identified FR, QR, constraint and criterion apply the specific processing logic found in the Extraction Rules by Type section below, during the extraction make sure to oblige the rules defined in Rules section.

# Extraction Rules by Type

## For All Types
   	- Assign a unique, strict sequential ID starting from R_01 (e.g., R_01, R_02, R_03...) regardless of type.
    - For each type, specify the page number from the document text (not the PDF page number) where the requirement starts.
    - For each type, specify the type (FR, QR, constraint or criterion).
    - If the source document misclassifies a requirement (e.g., an FR labeled as a QR), correct the classification and document your reasoning in the "fixes" field. Otherwise, leave "fixes" empty.
         
## For FR, QR and Constraints (Requirements)
    - In the case of a requirement expressed textually, the requirement is the full text.

## For FR
    - In the case of a user story AS A actor I WANT something IN ORDER TO whatever, the requirement will be I WANT something.
    - In the case of a functional requirement expressed in a use case specification, the requirement is the objective.
	- FR should have the following JSON Schema:
		{
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type": "<FR>",
        "description": "<FR in English>",
		“pageNumber”: “<Page number of the FR>”,
		“fixes”: [“Brief explanation if any mistakes corrected from the source document”]
        }

## For QR
    - For QRs, concept and categorization should be extracted from the document.
        - In case of a QR expressed using Volere, the concept is the QR type written in the Requirement part and categorization is Volere.
        - In case of a QR classified from ISO/IEC 25010, the concept is the subcharacteristic or, in case it is not stated, the characteristic. And the categorization is ISO/IEC 25010.
    - In case of a QR expressed using Volere with Description and Acceptance, the requirement is the Description contents.
	- QR should have the following JSON schema:
		{
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type": "<QR>",
        "description": "<QR in English>",
		“pageNumber”: “<Page number of the requirement>”,
        "concept": "<Type of QR (in English) >",
        "categorization": "<Volere or ISO/IEC 25010>",
		“fixes”: [“Brief explanation if any mistakes corrected from the source document”]
        }

## For Constraint
	- Constraint should have the following JSON Schema:
        {
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type": "<Constraint>",
        "description": "<Constraint in English>",
		“pageNumber”: “<Page number of the Constraint>”,
		“fixes”: [“Brief explanation if any mistakes corrected from the source document”]
        }	
        	
## For Criterion
    - In the case of a user history with several acceptance criteria, they should be added as separate requirements.
    - In case of a QR expressed using Volere with Description and Acceptance, the requirement is the Acceptance contents.
    - The link between an acceptance criterion and its related requirement is explicitly represented by stating the ID of the related requirement.
    - Criterion should have the following JSON schema:
		{
		"id":<Sequential Id shared with all types (R_01, R_02)>,
		"type":”<criterion>”
		"description": “<Actual acceptance criterion of the requirement in English>,>”,
		"pageNumber": “<Page number of the criterion>”,
        "relatedTo":”<Id of the related requirement>”,
        “fixes”: [“Brief explanation if any mistakes corrected from the source document”]
        }
        
# Rules:
- Ensure that all information is strictly supported by the document.
- Output should be given in JSON format as in Example Output section.
- Whole output must be English. If the source document is in another language, translation should be made while extracting to ensure all extracted fields are in English.
    - During translating, avoid to paraphrase, summarize, reword, or normalize phrasing.
- Avoid adding any extra explanation, just provide the required data.
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
- Avoid extracting post conditions or procedures as an acceptance criteria.
    Invalid criterion examples:
    "After entering the cridentials, user must access main view"
 
# Example Output (JSON)
 {
  "id": "R_01",
  "type": "FR",
  "description": "The system shall allow users to reset their password via email verification.",
  "pageNumber": "12",
  "fixes": [
    "Requirement was originally classified as a QR but was reclassified as an FR because it describes a system functionality."
  ]
},
{
  "id": "R_02",
  "type": "criterion",
  "description": "When a registered user requests a password reset, the system shall send a password reset email containing a valid verification link within 1 minute.",
  "pageNumber": "12",
  "relatedTo": "R_01",
  "fixes": []
},
{
  "id": "R_03",
  "type": "QR",
  "description": "The system shall respond to user requests within 2 seconds under normal operating conditions.",
  "pageNumber": "15",
  "concept": "12a. Speed and latency",
  "categorization": "Volere",
  "fixes": [ ]
},
{
  "id": "R_04",
  "type": "criterion",
  "description": "During performance testing with up to 1,000 concurrent users, 95% of requests shall complete within 2 seconds.",
  "pageNumber": "15",
  "relatedTo": "R_03",
  "fixes": []
}
    ... 
    """

    REQUIREMENT_SPLITTING_PROMPT = """
    # Objective:
    You are an expert software requirements analyst. You are given a JSON of already extracted requirements. Your task is to review each requirement and split it into multiple atomic requirements ONLY when it clearly expresses more than one independent need.

    # Instructions:
    1. Process each requirement in the input JSON one by one.
    2. Decide whether the requirement expresses a single need (atomic) or multiple needs (compound). Obligate the rules defined by the Rules section.
       - If atomic, keep it unchanged (same id and same field values).
       - If compound, split it into the minimum number of atomic requirements, one per distinct need. Preserve the original sentence structure for each split part so each resulting requirement reads as a complete, standalone statement (repeat the shared subject/predicate as needed). Keep the original id as the base and append sequential lowercase letters (R_01 -> R_01a, R_01b, R_01c ...).

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

    # Rules:
    - Preserve the original order of requirements.

    ## Avoid splitting:
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
          "description": "..."
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
    - Remove a criterion that merely restates its related requirement without adding any new, testable detail (i.e. it is basically the same as the requirement).
    - Remove a criterion that is not measurable or verifiable (it states no observable outcome, condition, threshold, metric, or acceptance condition that can be tested).
    - Remove a criterion that only states a post condition or a procedure / sequence of steps rather than an acceptance condition (e.g. "After entering the credentials, the user must access the main view.").
    - Keep every criterion that adds a concrete, measurable, or verifiable acceptance condition to its related requirement.
    - When in doubt, keep the criterion.
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
    You are an expert software architect and system design analyst. Extract the Architectural Units (the concrete building blocks of the system) from the provided software document and its accompanying images (diagrams). Do NOT extract the architectural or design Patterns themselves (those are produced by a separate prompt), but DO extract the concrete units a pattern is composed of — its layers, components and services (e.g. the View, ViewModel and Model parts of an MVC / MVVM frontend are Components and must be extracted here).

# Instructions
    1. Carefully read the entire document.
    2. Carefully inspect every provided image (architecture diagrams, deployment diagrams, etc.). Diagrams are a primary source: every box is usually a unit and every arrow/line is usually a Connector. Communications described only in the text ("X communicates with Y", "interacts with", "connects to", "calls", "sends to", "mediates between", "acts as an intermediary between") are Connectors too — extract them even when they are not drawn. When one unit is said to mediate, link, or sit between two others (e.g. "A is an intermediary between B and C"), output one Connector for each pair it connects (A–B and A–C).
    3. Identify ALL Architectural Units across the ENTIRE document, not only the ones drawn in the main diagram. A system is usually presented through several complementary views — e.g. a deployment / infrastructure view, a runtime or microservices view, a frontend component structure, and a backend layering — and these often live in different sections or in prose rather than in a single diagram. Extract the units from every such view and every section; treat the diagram as one source among several and never let it limit what you extract.
    4. Decompose the findings to the finest grain — do not merge or summarise several elements into one. When a unit is itself broken down into named sub-parts, or when several units are presented together as a list (e.g. a set of layers, the parts of a UI / presentation pattern, or a list of external services the system uses), extract each sub-part and each listed item as its own unit.
    5. ALWAYS write every output field in English. If the document is in another language (e.g. Spanish or Catalan), translate every "name" and "description" into English as you extract; never leave a name or description in the source language (e.g. translate "Plataforma Web" to "Web Platform", "Base de dades" to "Database").

# Type Definitions (choose exactly one type per unit; when a unit could fit two types, decide with the "NOT this type" lines below — a specific named product or tool is always a Technology, and an independently running or externally integrated capability is always a Service)

## Layer
    - Definition: a horizontal tier that groups units by a shared responsibility in a layered / n-tier architecture. Extract every named layer as its own unit.
    - Examples: presentation layer (controllers), business / business-logic layer (services), domain layer (models), data-access layer (repositories), contract layer (DTOs / mappers), service layer, persistence / data layer, infrastructure layer.
    - NOT a Layer: a single service or component that lives inside a layer (extract that as a Service / Component).

## Component
    - Definition: a concrete structural module of THIS system that is not exposed as an independently running service. When a structural or presentation pattern (e.g. MVC / MVVM, or a client split into parts) names its constituent parts, extract EACH named part as its own Component instead of the whole as one.
    - Examples: frontend, backend, database, View, ViewModel, Model (the parts of an MVC / MVVM design), controller, repository, cache, message broker.
    - NOT a Component: a specific named third-party product (that is a Technology); a module that runs independently or is an external integration (that is a Service).

## Service
    - Definition: a logical capability or independently running module of the system, including each microservice and each external / third-party service the system depends on, integrates with, or calls. Extract EVERY such service as its own unit — including ones that are only listed together, named in passing, or mentioned in prose — and never collapse several of them into one unit. Name each service by its capability or function (e.g. authentication, payment, storage, email, analytics), not by the product that implements it; when a named product provides that capability, additionally output the product as a separate Technology whose isPartOf is this Service.
    - Examples: an API gateway, a core / business service, a microservice, an authentication / authorization / payment / email / storage / analytics / logging / monitoring service, any external or third-party service the system integrates with or calls.
    - NOT a Service: the specific named product or vendor that implements the capability (that is a Technology); a horizontal tier (Layer).

## Device
    - Definition: a physical hardware endpoint or piece of equipment that participates in the system.
    - Examples: a sensor, a screen / display, a kiosk or player device, a mobile or desktop device, an IoT device, a hardware appliance.
    - NOT a Device: a network or medium such as the Internet (the communication is a Connector and any protocol is a Technology); a software process (Service / Other).

## Connector
    - Definition: a communication relationship between EXACTLY TWO units — it represents the connection of that single pair. Every arrow / line in a diagram and every communication described in the text is a Connector. A Connector has no name. Output a SEPARATE Connector for each pair of units; when the same communication method (same protocol, channel, or kind of call) links several different pairs, create one Connector per pair and never combine more than two units into a single Connector.
    - Examples: one layer communicating with one other layer, a service calling one other service, a service reading from one database, a client talking to a server.
    - NOT a Connector: the protocol used for the communication (a protocol is a Technology whose isPartOf is the Connector); either unit at the end of the communication.

## Technology
    - Definition: a specific, named product, framework, library, programming language, protocol, cloud service, or development / testing / monitoring tool used to build or run the system, including a named third-party product that provides an external service or integration. Extract every named technology, including ones mentioned only in the prose; when a product implements a capability, set its isPartOf to the Service that capability represents.
    - Examples: a communication protocol (e.g. HTTP, HTTPS, REST, TCP, WebSocket, gRPC), a programming language or framework, a database engine, a cloud service, a monitoring / analysis / testing tool, a named vendor product or library.
    - NOT a Technology: the generic capability the product provides for this system (that is a Service); the communication itself (Connector).

## Other
    - Definition: an external actor, role, or human / organisational entity that takes part in the architecture but does not fit any technical type above.
    - Examples: an end user, a customer, an administrator, an external organisation.
    - NOT Other: a physical hardware device (Device); a software service of the system (Service).

# Extraction Rules
    - Assign each Architectural Unit a strict sequential id: AU_01, AU_02, AU_03...
    - For each unit, specify the page number from the document text (not the PDF page number) where it is described. If it spans several pages, list them all.
    - For each unit, specify its type using exactly one of: "Layer", "Component", "Service", "Device", "Connector", "Technology" or "Other".
    - "isPartOf" is the list of OTHER Architectural Unit ids (AU_xx) this unit is contained by or belongs to. Build the containment hierarchy among units explicitly:
        - A Technology isPartOf the Service / Component / Connector that uses it.
        - A Service / Component isPartOf its Layer; if it has no Layer, isPartOf its parent Service / Component.
        - A Connector isPartOf the exactly two units it links.
        - Leave "isPartOf" as an empty list when no containing unit applies. Do NOT reference patterns here — relationships between units and patterns are out of scope for this step.
    - If the source document misclassifies a unit (e.g. a Service labelled as a Component), correct the classification and document your reasoning in the "fixes" field. Otherwise, leave "fixes" empty.
    - Architectural Unit should have the following JSON Schema:
        {
        "id": "<Sequential Architectural Unit id (AU_01, AU_02)>",
        "type": "<Layer | Component | Service | Device | Connector | Technology | Other>",
        "name": "<Name of the unit in English (leave empty for an unnamed Connector)>",
        "description": "<Description of the unit in English, supported by the document or an image>",
        "pageNumber": "<Page number(s) where the unit is described>",
        "isPartOf": ["<id of the unit this unit is part of>"],
        "fixes": ["<Brief explanation if any mistake was corrected from the source document>"]
        }

# Rules:
- Ensure every unit is strictly supported by the document or an image; do not output a unit or technology whose name or role does not actually appear in the source, and include an inferred unit only when the evidence is strong.
- Extract each distinct unit exactly once: do not output the same real unit twice under different names, and do not split one real unit into several.
- A Connector must connect EXACTLY two units; put exactly those two unit ids in its "isPartOf". If the same communication method links several pairs of units, output one separate Connector per pair — never list more than two units in one Connector.
- Never classify a protocol or technology as a Connector; protocols are Technology units.
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
      "type": "Connector",
      "name": "",
      "description": "Communication between the client and the server is carried out over the internet using the HTTP protocol and the REST API model.",
      "pageNumber": "41",
      "isPartOf": ["AU_01", "AU_02"],
      "fixes": []
    },
    {
      "id": "AU_04",
      "type": "Technology",
      "name": "HTTP",
      "description": "Communication between the two components is carried out over the internet using the HTTP protocol.",
      "pageNumber": "41",
      "isPartOf": ["AU_03"],
      "fixes": []
    },
    {
      "id": "AU_05",
      "type": "Technology",
      "name": "REST",
      "description": "Communication between the two components uses the REST API model.",
      "pageNumber": "41",
      "isPartOf": ["AU_03"],
      "fixes": []
    },
    {
      "id": "AU_06",
      "type": "Layer",
      "name": "Presentation Layer",
      "description": "It is the layer that is responsible for displaying information to the user.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_07",
      "type": "Layer",
      "name": "Business Layer",
      "description": "This layer is the core of the application and is responsible for processing all the information.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_08",
      "type": "Layer",
      "name": "Data Layer",
      "description": "It is the layer that is responsible for storing all the data.",
      "pageNumber": "41",
      "isPartOf": [],
      "fixes": []
    },
    {
      "id": "AU_09",
      "type": "Service",
      "name": "API Gateway Service",
      "description": "This service is responsible for the management, authentication and authorization of platform users and defines all the REST API routes.",
      "pageNumber": "44",
      "isPartOf": ["AU_07"],
      "fixes": []
    },
    {
      "id": "AU_15",
      "type": "Connector",
      "name": "",
      "description": "The presentation layer communicates with the business layer, more specifically with the API Gateway.",
      "pageNumber": "42",
      "isPartOf": ["AU_06", "AU_09"],
      "fixes": []
    }
  ]
}
    ...
    """

    PATTERN_EXTRACTION_PROMPT = """
# Objective
    You are an expert software architect and system design analyst. Extract the Patterns (the reusable design solutions the system is built upon) from the provided software document and its accompanying images (diagrams). Do NOT extract Architectural Units (layers, components, services, technologies, devices, connectors) — those are produced by a separate prompt.

# Instructions
    1. Carefully read the entire document.
    2. Carefully inspect every provided image (architecture diagrams, deployment diagrams, etc.).
    3. Identify all architectural styles and design solutions that are explicitly stated or clearly shown in the document and images.
    4. ALWAYS write every output field in English. If the document is in another language (e.g. Spanish or Catalan), translate every "name" and "description" into English as you extract.

# Extraction Rules
    - Assign each Pattern a strict sequential id: P_01, P_02, P_03...
    - For each pattern, specify the page number from the document text (not the PDF page number) where it is described. If it spans several pages, list them all.
    - For each pattern, specify its type using exactly one of:
        - "Architectural Pattern": a high-level structural organization of the system (e.g. Client-Server, Layered Architecture, Microservices, MVVM, Service-oriented, Cloud Architecture).
        - "Design Pattern": a lower-level software design solution used within units (e.g. API Gateway, Repository, Observer, Singleton, Shared Database, ORM, Component-based).
    - Extract EVERY architectural style and design solution named in the document, even when several co-exist (a system can be Client-Server AND Three-Layer AND Service-oriented AND Microservices at the same time). Do not stop at the first pattern you find.
    - "isPartOf" is the list of OTHER Pattern ids (P_xx) this pattern belongs to (e.g. "Three Layers" isPartOf "Client-Server"; a Design Pattern isPartOf the Architectural Pattern that introduces it). Leave it as an empty list when no parent pattern applies. Do NOT reference architectural units here — relationships between patterns and units are out of scope for this step.
    - If the source document misclassifies a pattern (e.g. a Design Pattern labelled as an Architectural Pattern), correct the classification and document your reasoning in the "fixes" field. Otherwise, leave "fixes" empty.
    - Pattern should have the following JSON Schema:
        {
        "id": "<Sequential Pattern id (P_01, P_02)>",
        "type": "<Architectural Pattern | Design Pattern>",
        "name": "<Name of the pattern in English>",
        "description": "<Why or where this pattern applies, supported by the document or an image>",
        "pageNumber": "<Page number(s) where the pattern is described>",
        "isPartOf": ["<id of the pattern this pattern is part of>"],
        "fixes": ["<Brief explanation if any mistake was corrected from the source document>"]
        }

# Rules:
- Ensure that all information is strictly supported by the document and the images. Do not output a pattern whose name does not actually appear in the source.
- Avoid inventing patterns that are not supported by the source; include an inferred item only when the evidence is strong.
- Do NOT extract Architectural Units; they are extracted separately.
- Output should be given in JSON format as in the Example Output section.
- The whole output must be English. If the source document is in another language, translation should be made while extracting to ensure all extracted fields are in English.
    - During translating, avoid to paraphrase, summarize, reword, or normalize phrasing.
- Avoid adding any extra explanation, just provide the required data.

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
