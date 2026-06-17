from typing import Annotated, Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from airbnb.search import search, SearchResults

app = FastAPI(title="Airbnb Explorer", version="1.0.0")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/search")
async def api_search(
    location: Annotated[str, Query(description="City, neighborhood, or address")],
    checkin: Annotated[Optional[str], Query(description="YYYY-MM-DD")] = None,
    checkout: Annotated[Optional[str], Query(description="YYYY-MM-DD")] = None,
    adults: Annotated[int, Query(ge=1, le=16)] = 1,
    children: Annotated[int, Query(ge=0, le=10)] = 0,
    infants: Annotated[int, Query(ge=0, le=5)] = 0,
    bedrooms: Annotated[Optional[int], Query(alias="min_bedrooms", ge=1, le=10)] = None,
    beds: Annotated[Optional[int], Query(alias="min_beds", ge=1, le=20)] = None,
    bathrooms: Annotated[Optional[float], Query(alias="min_bathrooms", ge=0.5)] = None,
    price_min: Annotated[Optional[int], Query(ge=1)] = None,
    price_max: Annotated[Optional[int], Query(ge=1)] = None,
    min_rating: Annotated[Optional[float], Query(ge=1.0, le=5.0)] = None,
    min_reviews: Annotated[Optional[int], Query(ge=1)] = None,
) -> JSONResponse:
    try:
        results: SearchResults = await search(
            location=location,
            checkin=checkin,
            checkout=checkout,
            adults=adults,
            children=children,
            infants=infants,
            min_bedrooms=bedrooms,
            min_beds=beds,
            min_bathrooms=bathrooms,
            price_min=price_min,
            price_max=price_max,
            min_rating=min_rating,
            min_reviews=min_reviews,
        )
        return JSONResponse(
            content={
                "location": results.location,
                "checkin": results.checkin,
                "checkout": results.checkout,
                "count": results.count,
                "listings": [l.to_dict() for l in results.listings],
            }
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except RuntimeError as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": f"Unexpected error: {exc}"})



@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
