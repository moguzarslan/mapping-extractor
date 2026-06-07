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
       - If compound, split it into the minimum number of atomic requirements, one per distinct need. Preserve the original sentence structure for each split part so each resulting requirement reads as a complete, standalone statement (repeat the shared subject/predicate as needed).
    3. Id assignment:
       - When a requirement is split, keep the original id as the base and append sequential lowercase letters (R_01 -> R_01a, R_01b, R_01c ...).
       - When a requirement is NOT split, keep its original id unchanged (R_01 stays R_01).
    4. Preserve the original order of requirements.

    # Example Split:
    - {
        "id": "R_01",
        "description": "The system shall send notifications by email and SMS."
      }
      ->
      {
        "id": "R_01a",
        "description": "The system shall send notifications by email."
      },
      {
        "id": "R_01b",
        "description": "The system shall send notifications by SMS."
      }

    # Rules:
    - Avoid splitting when the conjunction joins parts of a single indivisible need (e.g. "username and password" forming one credential, "save and exit" as one action if treated atomically in the source).
    - Avoid splitting closely related, paired, or opposite actions on the same target (e.g. "create/update/delete", "add or remove", "enable or disable", "assign or revoke", "grant or deny").
    - Avoid splitting an enumeration that defines the allowed values, permitted states, options, or range of a single attribute (e.g. "The order status can only be open or closed."); the "and"/"or" lists a value domain, not separate needs. Likewise, do not split an illustrative or parenthetical list of examples (e.g. "compatible with common browsers (Chrome, Firefox, Safari)" is one compatibility requirement).
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

    ARCHITECTURE_EXTRACTION_PROMPT = """
    Objective:
    You are an expert software architect and system design analyst.
    Your task is to analyze the provided software system document and extract the architecture at 3 distinct levels:
    1. Architectural Patterns
    2. Components
    3. Design Patterns
    
    Definitions:
    - Architectural Patterns: High-level structural organization of the system, such as microservices, layered architecture, client-server, event-driven architecture, hexagonal architecture, MVC, service-oriented architecture, etc.
    - Components: Concrete system building blocks or modules, such as frontend, backend service, API gateway, authentication service, database, message broker, cache, reporting module, etc.
    - Design Patterns: Lower-level software design solutions used within components, such as Strategy, Factory, Observer, Repository, Adapter, Singleton, Builder, etc.
        
    Instructions:
    1. Carefully read the provided entire document.
    2. Carefully inspect provided images.
    3. Identify all architectural information mentioned explicitly or strongly implied by the document.
    4. Separate the findings into the following 3 levels:
       - Architectural Patterns
       - Components
       - Design Patterns
    5. For each component, provide:
       - name
       - its role in the overall system
       - its technical implementation details
       - its communication and interactions with other components
       - page number reference(s)
    6. For each architectural pattern, provide:
       - pattern name
       - why it applies to the system
       - page number reference(s)
    7.. For each design pattern, provide:
       - pattern name
       - where/how it is used in the system
       - which component(s) it is associated with
       - page number reference(s)

    Rules:
    - Ensure that all information is strictly supported by the document and images.
    - Avoid inventing patterns or components that are not supported by the source.
    - If a pattern is only inferred, include it only when the evidence is strong.
    - Keep architectural patterns, components, and design patterns strictly separated.
    
    Output Format (JSON):
    {
      "architectural_patterns": [
        {
          "pattern_name": "<name>",
          "explanation": "<why this architectural pattern applies>",
          "page_number": ["<page number>", "<page number>"]
        }
      ],
      "components": [
        {
          "component_name": "<name>",
          "description": {
            "role": {
              "explanation": "<role in the overall system>",
              "page_number": ["<page number>"]
            },
            "technical_details": {
              "explanation": "<technical implementation details>",
              "page_number": ["<page number>"]
            },
            "communication": {
              "explanation": "<interactions with other components>",
              "page_number": ["<page number>"]
            }
          }
        }
      ],
      "design_patterns": [
        {
          "pattern_name": "<name>",
          "associated_components": ["<component name>"],
          "explanation": "<how this design pattern is used>",
          "page_number": ["<page number>"]
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
