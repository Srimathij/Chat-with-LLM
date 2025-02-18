import os
import requests
from bs4 import BeautifulSoup
import streamlit as st
from langchain_core.messages import AIMessage, HumanMessage
from langchain_community.document_loaders import WebBaseLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from dotenv import load_dotenv
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.chains import create_retrieval_chain
from groq import Groq
from sentence_transformers import SentenceTransformer

# Load environment variables (e.g., GROQ_API_KEY if necessary)
load_dotenv()
groq_api_key = os.getenv("GROQ_API_KEY")

# Initialize Groq client
client = Groq()

# Embedder using SentenceTransformer for real embeddings
class RealEmbedder:
    def __init__(self):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')

    def embed_documents(self, texts):
        return self.model.encode(texts).tolist()

    def embed_query(self, text):
        return self.model.encode([text]).tolist()[0]

# Function to fetch all internal links from a given URL
def get_all_links(base_url):
    links = set()
    try:
        response = requests.get(base_url)
        soup = BeautifulSoup(response.text, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            # Ensure the link is internal
            if href.startswith("/"):
                href = base_url.rstrip("/") + href
            if base_url in href:  # Same domain check
                links.add(href)
    except Exception as e:
        print(f"Error fetching links from {base_url}: {e}")
    return list(links)

# Function to fetch and process the vector store from a URL
def get_vectorstore_from_url(url):
    # Discover all relevant links
    urls = get_all_links(url)
    documents = []

    # Load content from all discovered links
    for link in urls:
        try:
            loader = WebBaseLoader(link)
            documents.extend(loader.load())
        except Exception as e:
            print(f"Error loading content from {link}: {e}")

    # Split the documents into chunks
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1500, chunk_overlap=300)
    document_chunks = text_splitter.split_documents(documents)

    # Create a vector store using real embeddings
    vector_store = Chroma.from_documents(document_chunks, RealEmbedder())
    return vector_store

# Function to call Groq Llama model for response generation
def call_groq_llama(prompt):
    """Use Groq's Llama model to generate responses."""
    try:
        # Request a completion from Groq's Llama model
        completion = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[{"role": "user", "content": prompt}],
            temperature=1,
            max_completion_tokens=1024,
            top_p=1,
            stream=True,  # Stream response
            stop=None,
        )

        # Stream and print chunks of the response
        response_content = ""
        for chunk in completion:
            # Check and append the content of the chunk
            response_content += chunk.choices[0].delta.content or ""
        
        return response_content

    except Exception as e:
        print(f"Error with Groq request: {e}")
        return "Sorry, an error occurred while processing your request."

# Create the conversation retriever chain
def get_conversational_rag_chain(vector_store): 
    retriever = vector_store.as_retriever(search_type="similarity", search_kwargs={"k": 10})

    def combine_context(context_docs, chat_history, user_input):
        """Combine documents and use Groq for the final answer."""
        context = "\n\n".join(f"Section {i + 1}: {doc.page_content}" for i, doc in enumerate(context_docs))
        full_prompt = (
            "You are a highly accurate assistant. Use only the provided website context to answer the user's questions. "
            "Be precise and prioritize information about leadership, CEO, directors, or company-specific details. "
            "If the information isn't found, explicitly mention that it's not available.\n\n"
            f"Website Context: {context}\n\n"
            f"Chat History: {chat_history}\n\nUser Question: {user_input}"
        )
        return call_groq_llama(full_prompt)

    return lambda input_dict: {
        "answer": combine_context(
            retriever.get_relevant_documents(input_dict["input"]),
            input_dict["chat_history"], 
            input_dict["input"]
        )
    }

# Get response for user input
def get_response(user_input):
    conversation_rag_chain = get_conversational_rag_chain(st.session_state.vector_store)
    
    response = conversation_rag_chain({
        "context": "",  # Context fetched dynamically in real implementation
        "chat_history": st.session_state.chat_history,
        "input": user_input
    })
    
    return response['answer']

# Streamlit app configuration
st.set_page_config(page_title="Website Chat Agent", page_icon="🤖")
st.title("Website Chat Agent")

# Sidebar for website URL input
# Sidebar for website URL input
with st.sidebar:
    st.header("Settings")
    website_url = st.text_input("Website URL")

    # "Change URL" button to update the website for testing
    if st.button("Change URL"):
        if website_url:
            # Reset vector store and chat history for a fresh start
            st.session_state.vector_store = None
            st.session_state.chat_history = [
                AIMessage(content="Hello there! I'm your friendly digital assistant. How can I help you today?")
            ]
            
            # Build a new vector store for the provided URL
            st.session_state.vector_store = get_vectorstore_from_url(website_url)
            
            # Optionally rerun to refresh everything
            st.experimental_rerun()
        else:
            st.warning("Please enter a valid URL.")


# Check if a URL is provided in the main app area
if website_url is None or website_url == "":
    st.info("Please enter a website URL in the sidebar")

else:
    # Initialize session state if not already set (this only runs if URL is provided)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = [
            AIMessage(content="Hello there! I'm your friendly digital assistant, here to unlock insights and spark ideas. How can I help you today?"),
        ]
    if "vector_store" not in st.session_state:
        # Fetch content from URL and build the vector store
        st.session_state.vector_store = get_vectorstore_from_url(website_url)

    # User input (ask a question)
    user_query = st.chat_input("What is up?")
    if user_query is not None and user_query != "":
        # Get response based on the question and context
        response = get_response(user_query)
        # Update chat history
        st.session_state.chat_history.append(HumanMessage(content=user_query))
        st.session_state.chat_history.append(AIMessage(content=response))

    # Display the conversation history
    for message in st.session_state.chat_history:
        if isinstance(message, AIMessage):
            with st.chat_message("AI"):
                st.write(message.content)
        elif isinstance(message, HumanMessage):
            with st.chat_message("Human"):
                st.write(message.content)
