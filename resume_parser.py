import os
import re
from PyPDF2 import PdfReader
from docx import Document
from collections import Counter

# A lightweight standard dictionary of common tech/business keywords.
# This prevents needing a 5GB ML model just to find if someone knows Python.
TECH_KEYWORDS = {
    # Languages / Core
    "python", "java", "javascript", "typescript", "c++", "c#", "ruby", "php", "go", "rust", "swift", "kotlin", "sql", "html", "css",
    # Frameworks & Libs
    "react", "angular", "vue", "django", "flask", "fastapi", "spring", "node.js", "express", "tensorflow", "pytorch", "keras", "pandas", "numpy", "scikit-learn", "dotnet", ".net",
    # Cloud & DevOps
    "aws", "azure", "gcp", "google cloud", "docker", "kubernetes", "jenkins", "gitlab", "github actions", "terraform", "ansible", "linux", "unix", "bash", "powershell",
    # Databases
    "mysql", "postgresql", "mongodb", "redis", "elasticsearch", "cassandra", "oracle", "sql server", "snowflake", "bigquery", "redshift",
    # Roles & Concepts
    "machine learning", "artificial intelligence", "data science", "data engineering", "backend", "frontend", "fullstack", "agile", "scrum", "product management", "project management", "marketing", "sales", "finance", "accounting", "hr", "operations", "ui/ux", "design", "qa", "testing", "devops", "security", "cybersecurity", "analytics"
}

def extract_text_from_pdf(filepath: str) -> str:
    """Extract all text from a PDF file."""
    text = ""
    try:
        reader = PdfReader(filepath)
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                text += page_text + " "
    except Exception as e:
        print(f"Error reading PDF {filepath}: {e}")
    return text

def extract_text_from_docx(filepath: str) -> str:
    """Extract all text from a DOCX file."""
    text = ""
    try:
        doc = Document(filepath)
        for para in doc.paragraphs:
            text += para.text + " "
    except Exception as e:
        print(f"Error reading DOCX {filepath}: {e}")
    return text

def parse_resume_for_keywords(filepath: str, top_n: int = 5) -> list:
    """
    Reads a resume (PDF/DOCX) and returns the top N matched technical/business keywords.
    """
    if not os.path.exists(filepath):
        print(f"❌ Resume file not found at: {filepath}")
        return []

    ext = filepath.lower().split('.')[-1]
    if ext == 'pdf':
        text = extract_text_from_pdf(filepath)
    elif ext in ['doc', 'docx']:
        text = extract_text_from_docx(filepath)
    else:
        print(f"❌ Unsupported file format: {ext}")
        return []

    if not text.strip():
        print("❌ Could not extract any text from the resume.")
        return []

    # Clean text: lowercase, remove weird punctuation keeping basic alphanumeric
    clean_text = re.sub(r'[^a-zA-Z0-9\+#\.\- ]', ' ', text.lower())
    words = clean_text.split()
    
    # We want to match unigrams (like "python") and bigrams (like "machine learning")
    # Generate bigrams:
    bigrams = []
    for i in range(len(words) - 1):
        bigrams.append(f"{words[i]} {words[i+1]}")
        
    all_tokens = words + bigrams
    
    # Count how many times each known keyword appears
    matched_counts = Counter()
    for token in all_tokens:
        if token in TECH_KEYWORDS:
            matched_counts[token] += 1
            
    # Also handle some edge cases for weird spacing
    for kw in TECH_KEYWORDS:
        if kw not in matched_counts:
            # If the exact string appears in the raw block (e.g., node.js might lose the dot in split)
            if kw in clean_text:
                # Add a base count so it gets recognized
                matched_counts[kw] += clean_text.count(kw)

    if not matched_counts:
        return []

    # Get the most frequent keywords
    most_common = [item[0] for item in matched_counts.most_common(top_n)]
    return most_common

if __name__ == "__main__":
    # Test block
    test_file = "my_resume.pdf"
    print(f"Testing with {test_file}...")
    keywords = parse_resume_for_keywords(test_file)
    print(f"Extracted Keywords: {keywords}")
