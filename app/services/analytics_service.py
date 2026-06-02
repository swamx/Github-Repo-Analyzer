import logging
from collections import defaultdict
from datetime import datetime
from statistics import mean, median
from typing import Dict, List, Optional

from app.models.schemas import EngineerMetrics, RepositoryMetrics

logger = logging.getLogger(__name__)


class AnalyticsService:
    """Service for analyzing GitHub repository metrics"""

    def generate_metrics(
        self,
        github_data: Dict,
        owner: str,
        repo: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> RepositoryMetrics:
        try:
            repo_data = github_data["data"]["repository"]
        except (KeyError, TypeError) as e:
            raise ValueError(f"Invalid GitHub data structure: {e}") from e

        prs = repo_data.get("pullRequests", {}).get("nodes", [])
        issues = repo_data.get("issues", {}).get("nodes", [])

        engineers = defaultdict(
            lambda: {
                "prs_merged": 0,
                "prs_created": 0,
                "reviews": 0,
                "issues_closed": 0,
                "issues_created": 0,
                "total_cycle_hours": 0,
                "cycle_times": [],
                "review_latency_hours": [],
                "contribution_score": 0.0,
            }
        )

        all_cycle_times = []
        all_review_latencies = []

        for pr in prs:
            try:
                author = (pr.get("author") or {}).get("login", "unknown")
                created_str = pr.get("createdAt")
                merged_str = pr.get("mergedAt")

                if not created_str or not merged_str:
                    continue

                created = self._parse_datetime(created_str)
                merged = self._parse_datetime(merged_str)

                if not self._in_time_range(created, start_time, end_time):
                    continue

                cycle_hours = (merged - created).total_seconds() / 3600
                engineers[author]["prs_merged"] += 1
                engineers[author]["total_cycle_hours"] += cycle_hours
                engineers[author]["cycle_times"].append(cycle_hours)
                all_cycle_times.append(cycle_hours)

                reviews = pr.get("reviews", {}).get("nodes", [])
                if reviews:
                    review_times = []
                    for review in reviews:
                        try:
                            review_created_str = review.get("createdAt")
                            if review_created_str:
                                review_created = self._parse_datetime(review_created_str)
                                review_times.append(review_created)
                        except (ValueError, TypeError) as e:
                            logger.debug("Skipping unparseable review timestamp: %s", e)

                    if review_times:
                        first_review = min(review_times)
                        latency = (first_review - created).total_seconds() / 3600
                        if latency >= 0:
                            engineers[author]["review_latency_hours"].append(latency)
                            all_review_latencies.append(latency)

                for review in reviews:
                    reviewer = (review.get("author") or {}).get("login", "unknown")
                    if reviewer != "unknown":
                        engineers[reviewer]["reviews"] += 1

            except Exception as e:
                logger.warning("Error processing PR author=%s: %s", pr.get("author", {}).get("login"), e)
                continue

        for issue in issues:
            try:
                author = (issue.get("author") or {}).get("login", "unknown")
                created_str = issue.get("createdAt")
                closed_str = issue.get("closedAt")

                if not created_str or not closed_str:
                    continue

                created = self._parse_datetime(created_str)
                closed = self._parse_datetime(closed_str)

                if not self._in_time_range(created, start_time, end_time):
                    continue

                engineers[author]["issues_created"] += 1
                engineers[author]["issues_closed"] += 1

            except Exception as e:
                logger.warning("Error processing issue author=%s: %s", issue.get("author", {}).get("login"), e)
                continue

        avg_cycle_time = mean(all_cycle_times) if all_cycle_times else 0.0
        median_cycle_time = median(all_cycle_times) if all_cycle_times else 0.0
        avg_review_latency = mean(all_review_latencies) if all_review_latencies else 0.0
        median_review_latency = median(all_review_latencies) if all_review_latencies else 0.0

        top_contributors = []
        top_reviewers = []

        for username, metrics in engineers.items():
            total_contributions = (
                metrics["prs_merged"] * 2
                + metrics["issues_closed"] * 1
                + metrics["reviews"] * 0.5
            )
            max_score = max(
                max(
                    (e["prs_merged"] * 2 + e["issues_closed"] + e["reviews"] * 0.5)
                    for e in engineers.values()
                )
                if engineers
                else 1,
                1,
            )
            contribution_score = total_contributions / max_score

            engineer_metric = EngineerMetrics(
                username=username,
                prs_merged=metrics["prs_merged"],
                prs_created=metrics["prs_created"],
                reviews_completed=metrics["reviews"],
                issues_closed=metrics["issues_closed"],
                issues_created=metrics["issues_created"],
                total_cycle_hours=metrics["total_cycle_hours"],
                avg_cycle_hours=mean(metrics["cycle_times"]) if metrics["cycle_times"] else None,
                review_latency_hours=metrics["review_latency_hours"],
                avg_review_latency=mean(metrics["review_latency_hours"]) if metrics["review_latency_hours"] else None,
                contribution_score=contribution_score,
            )

            if metrics["prs_merged"] > 0:
                top_contributors.append(engineer_metric)
            if metrics["reviews"] > 0:
                top_reviewers.append(engineer_metric)

        top_contributors.sort(key=lambda x: x.contribution_score, reverse=True)
        top_reviewers.sort(key=lambda x: x.reviews_completed, reverse=True)
        top_contributors = top_contributors[:10]
        top_reviewers = top_reviewers[:10]

        velocity_trend = self._calculate_velocity_trend(all_cycle_times)
        quality_score = self._calculate_quality_score(avg_cycle_time, median_cycle_time)

        if start_time and end_time:
            analysis_period = f"{start_time.date()} to {end_time.date()}"
        elif start_time:
            analysis_period = f"from {start_time.date()}"
        elif end_time:
            analysis_period = f"until {end_time.date()}"
        else:
            analysis_period = "all time"

        logger.debug(
            "generate_metrics owner=%s repo=%s prs=%d issues=%d contributors=%d",
            owner, repo, len(prs), len(issues), len(top_contributors),
        )

        return RepositoryMetrics(
            owner=owner,
            repo=repo,
            analysis_period=analysis_period,
            total_prs_merged=len(prs),
            total_issues_closed=len(issues),
            total_reviews=sum(len(pr.get("reviews", {}).get("nodes", [])) for pr in prs),
            avg_cycle_time_hours=avg_cycle_time,
            median_cycle_time_hours=median_cycle_time,
            avg_review_latency_hours=avg_review_latency,
            median_review_latency_hours=median_review_latency,
            unique_contributors=len([e for e in engineers.values() if e["prs_merged"] > 0]),
            unique_reviewers=len([e for e in engineers.values() if e["reviews"] > 0]),
            top_contributors=top_contributors,
            top_reviewers=top_reviewers,
            velocity_trend=velocity_trend,
            quality_score=quality_score,
        )

    @staticmethod
    def _parse_datetime(datetime_str: str) -> datetime:
        return datetime.fromisoformat(datetime_str.replace("Z", "+00:00"))

    @staticmethod
    def _in_time_range(
        dt: datetime,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
    ) -> bool:
        if start_time and dt < start_time:
            return False
        if end_time and dt > end_time:
            return False
        return True

    @staticmethod
    def _calculate_velocity_trend(cycle_times: List[float]) -> str:
        if len(cycle_times) < 2:
            return "insufficient_data"

        mid = len(cycle_times) // 2
        first_half = cycle_times[:mid]
        second_half = cycle_times[mid:]

        avg_first = mean(first_half) if first_half else 0
        avg_second = mean(second_half) if second_half else 0

        if avg_first == 0:
            return "insufficient_data"

        change_percent = ((avg_second - avg_first) / avg_first) * 100

        if change_percent > 10:
            return "decreasing"
        elif change_percent < -10:
            return "increasing"
        else:
            return "stable"

    @staticmethod
    def _calculate_quality_score(avg_cycle_time: float, median_cycle_time: float) -> float:
        baseline = 24.0
        if avg_cycle_time == 0:
            return 1.0
        return min(baseline / (avg_cycle_time + baseline), 1.0)
