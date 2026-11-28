FROM nineseconds/mtg:2 AS mtg

FROM alpine:3.20
RUN apk add --no-cache bash ca-certificates
COPY --from=mtg /mtg /usr/local/bin/mtg
COPY start.sh /start.sh
RUN sed -i 's/\r$//' /start.sh && chmod +x /start.sh

# keep memory use low (cheaper on Railway)
ENV GOMEMLIMIT=96MiB

CMD ["/start.sh"]
