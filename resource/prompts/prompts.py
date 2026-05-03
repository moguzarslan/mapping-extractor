class Prompts:
    REQUIREMENT_EXTRACTION_PROMPT = """
    Objective:
    You are an expert software requirements analyst. Extract functional and non-functional requirements from the provided software product document.
    
    Instructions:
    1. Review the entire document.
    2. Identify all functional, non-functional requirements and constraints.
    3. Extract the exact requirement statements as in the document.
    4. If a requirement is written in a language other than English, translate it into clear English.
    5. Original meaning of the requirement has to be preserved
    6. For each requirement give the page number the requirement has found
    7. For each requirement give the requirement type (functional, non-functional or constraint)
    8. Give page number reference for each section for each component. 
    
    Rules:
    - Ensure that all information is strictly supported by the document.
    - Give the output in json format.
    - Give the whole output in English.
    - Avoid duplicates — each requirement should be listed once. 
    - Avoid adding any extra explanation, just provide the exact requirement text.

    Example Output (JSON):
    {
      "requirements": [
        {
          "requirement": "<Requirement>",
          "page_number": "<Page Number>"
        },
        {
          "requirement": "...",
          "page_number": "..."
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
       - A clear explanation of how the architectural item fulfills or contributes to each requirement.    3. Establish many-to-many relationships where applicable (a component can map to multiple requirements and vice versa).
    3. Base your mapping strictly on the provided data — avoid inventing unsupported relationships.
    4. Use architectural item explanations to justify mappings.
    
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
    REQUIREMENT_VALIDATION_PROMPT = """
    Objective:
    You are a strict evaluator. Your task is to validate a JSON output extracted from a source document.
    
    Inputs:
    1. Source document with page numbers.
    2. JSON output containing extracted requirements. Each requirement includes:
       - requirement title/name
       - requirement sentence/explanation
       - requirement type/category
       - referenced page number(s)
    
    Instructions for each requirement:    
    1. Calculate the validation score with following guidelines:
        - Requirement is found in the referenced page/s (1 point)
        - Requirement sentence is only include the requirement (does not have any justification or invented information) (2 point)
        - Requirement meaning is fully preserved (translations or rephrasing is allowed only if meaning is preserved)(5 point)
        - Requirement type set correctly (2 point)
        
    Rules:
    - Be strict about invented information.
    - Avoid rewarding explanations that sound plausible but are not supported by the referenced page.
    - Translations are allowed, but the original meaning must be preserved.
    - Ensure whole output is given in English (Including requirement titles/sentences).
    - Justifications will only be about deducted points if there is no deduction, it must be empty.

    
    Output Format (JSON):
    {
      "evaluations": [
        {
          "requirement": "string",
          "referenced_pages": ["string"],
          "validationScore": number,
          "justification": "Brief explanation about deducted points (if there is any)."
        }
      ]
    }
        ... 
        """
    ARCHITECTURE_VALIDATION_PROMPT = """
    Objective:
    You are a strict evaluator. Your task is to validate an architecture JSON extracted from a software document.
    
    Inputs:
    1. Source document.
    2. Architecture JSON containing:
       - architectural patterns
       - components
       - design patterns  
    Each item includes explanations and referenced page number(s).
    
    Instructions:
    1. For each architectural pattern, check pattern exists and is supported by the document.      
       Assign validation score:  
       - Role of the component is correct  (5 point)
       - There is no missing information that can change component's role drastically. (2 point)
       - There is no invented information. (2 point)
       - Reference pages are correctly identified. (1 point) 
    
    2. For each component, go to every referenced page for each sub-field (role, technical details, communication).  
       Evaluate role explanation:  
       Assign role score:  
       - Role of the component is correct  (5 point)
       - There is no missing information that can change component's role drastically. (2 point)
       - There is no invented information. (2 point)
       - Reference pages are correctly identified. (1 point)
       
       Evaluate technical details explanation:  
       Assign technical details score:  
       - Technical details are correct and supported by document (5 point)
       - There is no missing technical aspect that is majorly considered in the paper. (2 point)
       - There is no invented information. (2 point)
       - Reference pages are correctly identified. (1 point)
       
       Evaluate communication explanation:  
       Assign communication score:  
       - Communication flows are correct and supported by document (5 point)
       - There is no missing communication flow. (2 point)
       - There is no invented information. (2 point)
       - Reference pages are correctly identified. (1 point)

    
    3. For each design pattern, go to every referenced page and validate both association and explanation.  
       Assign validation score:  
       - Associated components are correctly identified. (2 point)
       - Design pattern is correct and supported by document (5 point)
       - There is no invented information. (2 point)
       - Reference pages are correctly identified. (1 point)

    
    Rules
    - Avoid assuming correctness if not explicitly supported.  
    - Be strict about invented or exaggerated explanations.  
    - In justifications only explain the reason for deducted points.  
    - Ensure whole output is given in English.
    
    Output JSON:
    
    Return only valid JSON in the following format:
    
    {
      "architectural_patterns": [
        {
          "patternName": "string",
          "validationScore": number,
          "justification": "Brief explanation of deducted points (If there is any)"
        }
      ],
      "components": [
        {
          "componentName": "string",
          "roleScore": number,
          "technicalDetailsScore": number,
          "communicationScore": number,
          "justification": "Brief explanation of deducted points (If there is any)"
        }
      ],
      "design_patterns": [
        {
          "patternName": "string",
          "validationScore": number,
          "justification": "Brief explanation of deducted points (If there is any)"
        }
      ]
    }
    """

    MAPPING_VALIDATION_PROMPT = """
    Objective:
    You are a strict evaluator. Your task is to validate an architecture JSON extracted from a software document.
    
    Inputs:
    1. Source document.
    2. Mappings JSON containing:
       - Mappings between requirements and architectural items that can be:
         Architectural Pattern, Component or a Design Pattern
    
    Instructions:
    1. For each architectural pattern, check if mappings are exist and is supported by the document.      
       Assign validation score:  
       - Related requirements are supported by the document  (4 point)
       - Explanation is detailed enough and supported by the document (4 point)
       - There is no invented information. (2 point)
    2. For each component, check if mappings are exist and is supported by the document.      
       Assign validation score:  
       - Related requirements are correct and supported by the document  (4 point)
       - Explanation is detailed enough and supported by the document (4 point)
       - There is no invented information. (2 point)
    3. For each design pattern, check if mappings are exist and is supported by the document.      
       Assign validation score:  
       - Related requirements are correct and supported by the document  (4 point)
       - Explanation is detailed enough and supported by the document (4 point)
       - There is no invented information. (2 point)
    
    Rules
    - Avoid assuming correctness if not explicitly supported.  
    - Be strict about invented or exaggerated explanations.  
    - You are restricted with the input json, avoid inventing any extra architectural patterns, components, and design patterns that are not in json.  
    - In justifications only explain the reason for deducted points. Give an empty justification if it gets full point 
    - Ensure whole output is given in English.
    
    Example Output JSON:    
    {
      "architectural_patterns": [
        {
          "pattern_name": "string",
          "validationScore": number,
          "justification": "Brief explanation of deducted points (If there is any)"
        }
      ],
      "components": [
        {
          "component_name": "string",
          "validationScore": number,
          "justification": "Brief explanation of deducted points (If there is any)"
        }
      ],
      "design_patterns": [
        {
          "pattern_name": "string",
          "validationScore": number,
          "justification": "Brief explanation of deducted points (If there is any)"
        }
      ]
    }
    """
