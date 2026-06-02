from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


# ==================== Request Models ====================

class AnalyzeRequest(BaseModel):
    """Request to analyze a GitHub repository"""
    repo_url: str = Field(..., description="GitHub repository URL (e.g., https://github.com/owner/repo)")
    start_time: Optional[datetime] = Field(None, description="Start time for analysis (ISO format)")
    end_time: Optional[datetime] = Field(None, description="End time for analysis (ISO format)")

    class Config:
        json_schema_extra = {
            "example": {
                "repo_url": "https://github.com/microsoft/vscode",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-12-31T23:59:59Z"
            }
        }


class ChatMessage(BaseModel):
    """Chat message in conversation"""
    role: str = Field(..., description="Message role: 'user' or 'assistant'")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request for chat interaction"""
    message: str = Field(..., description="User message")
    conversation_history: Optional[List[ChatMessage]] = Field(
        default=None,
        description="Previous conversation messages for context"
    )
    repo_url: Optional[str] = Field(
        None,
        description="GitHub repository URL for context"
    )
    start_time: Optional[datetime] = Field(None, description="Start time for repo analysis context")
    end_time: Optional[datetime] = Field(None, description="End time for repo analysis context")

    class Config:
        json_schema_extra = {
            "example": {
                "message": "What are the top contributors to this repo?",
                "repo_url": "https://github.com/microsoft/vscode",
                "conversation_history": []
            }
        }


# ==================== Metrics Models ====================

class EngineerMetrics(BaseModel):
    """Metrics for an individual engineer"""
    username: str
    prs_merged: int = 0
    prs_created: int = 0
    reviews_completed: int = 0
    issues_closed: int = 0
    issues_created: int = 0
    total_cycle_hours: float = 0.0
    avg_cycle_hours: Optional[float] = None
    review_latency_hours: List[float] = Field(default_factory=list)
    avg_review_latency: Optional[float] = None
    contribution_score: float = 0.0


class RepositoryMetrics(BaseModel):
    """Aggregated metrics for a repository"""
    owner: str
    repo: str
    analysis_period: str = Field(..., description="Time period analyzed (e.g., '2024-01-01 to 2024-12-31')")
    
    total_prs_merged: int = 0
    total_issues_closed: int = 0
    total_reviews: int = 0
    
    # Performance metrics
    avg_cycle_time_hours: float = 0.0
    median_cycle_time_hours: float = 0.0
    avg_review_latency_hours: float = 0.0
    median_review_latency_hours: float = 0.0
    
    # Team metrics
    unique_contributors: int = 0
    unique_reviewers: int = 0
    
    # Per-engineer breakdown
    top_contributors: List[EngineerMetrics] = Field(default_factory=list)
    top_reviewers: List[EngineerMetrics] = Field(default_factory=list)
    
    # Patterns
    velocity_trend: str = ""  # increasing, stable, decreasing
    quality_score: float = 0.0  # 0-1


# ==================== Summary Models ====================

class AnalysisSummary(BaseModel):
    """LLM-generated summary and insights"""
    summary: str = Field(..., description="High-level summary of repository metrics")
    key_findings: List[str] = Field(..., description="Key findings and patterns")
    performance_insights: Dict[str, str] = Field(..., description="Performance analysis by category")
    root_cause_hypotheses: List[str] = Field(..., description="Potential root causes for observed patterns")
    recommendations: List[str] = Field(..., description="Actionable recommendations")
    confidence_score: float = Field(..., description="Confidence in analysis (0.0-1.0)")


class AnalyzeResponse(BaseModel):
    """Response to repository analysis"""
    status: str = "success"
    metrics: RepositoryMetrics
    analysis: AnalysisSummary
    generated_at: datetime = Field(default_factory=datetime.utcnow)


# ==================== Chat Response Models ====================

class ChatResponse(BaseModel):
    """Response to chat message"""
    status: str = "success"
    message: str = Field(..., description="Assistant response")
    context: Optional[Dict[str, Any]] = Field(None, description="Context data used (metrics, analysis, etc.)")
    tool_calls: Optional[List[str]] = Field(None, description="Tools called to generate response")
    conversation_turn: int = Field(default=1, description="Turn number in conversation")


# ==================== Error Models ====================

class ErrorResponse(BaseModel):
    """Error response"""
    status: str = "error"
    error: str
    detail: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
