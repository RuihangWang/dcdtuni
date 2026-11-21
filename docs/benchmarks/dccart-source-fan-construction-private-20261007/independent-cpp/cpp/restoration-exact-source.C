                // New fan points must stay on a bit-literal axis support plane.
                // Keep the original numeric centroid for metrics/conservation;
                // only the coordinate passed to insertPoint is restored.
                point fanCentre=fc;
                const scalar fanAreaSqr=magSqr(av);
                bool finiteFan=poly.size()>=3 && std::isfinite(fanAreaSqr)
                    && fanAreaSqr>0;
                for (direction k=0;k<3;++k)
                    finiteFan=finiteFan && std::isfinite(fc[k]);
                for (label v:poly) for (direction k=0;k<3;++k)
                    finiteFan=finiteFan && std::isfinite(m.points[v][k]);
                if (finiteFan)
                {
                    direction supportAxis=0;label fixedAxes=0;
                    for (direction k=0;k<3;++k)
                    {
                        const scalar plane=m.points[poly[0]][k];bool fixed=true;
                        for (label v:poly)
                        {
                            const scalar value=m.points[v][k];
                            if (std::memcmp(&plane,&value,sizeof(scalar))!=0)
                            { fixed=false;break; }
                        }
                        if (fixed) { supportAxis=k;++fixedAxes; }
                    }
                    // A degenerate line/point or non-axis plane is not this grammar.
                    // Signed-zero mixtures are not a bit-literal fixed component.
                    if (fixedAxes==1)
                        fanCentre[supportAxis]=m.points[poly[0]][supportAxis];
                }
